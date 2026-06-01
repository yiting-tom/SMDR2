"""Pattern matching engine.

Given a template (one or more entities' point sets) and a drawing's entity
geometries, finds all matches under translation + rotation + mirror + scale
∈ [0.95, 1.05] + ε tolerance.

MVP approach:
- Per-entity shape signature (vertex count, path length, bbox-diagonal) for
  fast pre-filtering.
- Procrustes-style alignment using PCA principal axes (up to 4 mirror/180°
  variants) + Kabsch optimal rotation refinement.
- Chamfer distance under the recovered transform is the match score.
- Single-entity templates: enumerate every drawing entity as a candidate.
- Multi-entity templates: each drawing entity is a "seed", gather nearby
  entities into a candidate group, compare combined point clouds.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy.spatial import cKDTree

from app.library import Template


logger = logging.getLogger(__name__)


# ---- Tunables -----------------------------------------------------------
SCALE_MIN = 0.99
SCALE_MAX = 1.01
TOLERANCE_ABS = 0.2             # ε for chamfer (world units / mm)
VERTEX_COUNT_RATIO = 0.3       # candidate must be within ±25% vertex count
PATH_LENGTH_RATIO = 0.20        # ±20% path length (covers scale range + sampling noise)
RADIUS_RATIO = 0.20             # ±20% max-radius-from-centroid (rotation-invariant)
SIGMA_RATIO_TOL = 0.15          # absolute Δ on σ₂/σ₁ ∈ [0,1] — principal-axis aspect
# Above this σ₂/σ₁ a shape is treated as isotropic (circle, regular polygon,
# rounded square, cross) — its PCA principal-axis direction is numerically
# unstable across instances, so the multi-entity matcher's pose recovery
# falls back to 2-point centroid alignment instead of PCA-axes rotation.
# Conservative: real anisotropic shapes (narrow rect, L-shape) sit < 0.7.
ISOTROPIC_SIGMA_RATIO_THRESHOLD = 0.95
# Decimal digits kept when bucketing CIRCLE radii for the exact-radius fast
# path. 10⁻⁶ corresponds to 1 nm precision in mm-unit DXFs — six orders of
# magnitude finer than any meaningful BGA / SMD design tolerance, yet loose
# enough to absorb the ~10⁻¹¹-mm noise that CAD operations (block inserts,
# transforms) accumulate on large-radius circles.
#
# Diagnostic that drove this value: a real packaging DXF with 400,768
# bit-identical-by-design CIRCLE entities at r ≈ 189.957671 mm split into
# *two* buckets at the original 10-digit precision (365k vs 35k), because
# the noise span was 4.5e-11 mm — straddling the 10⁻¹⁰ boundary. At digit 6
# the same data collapses to a single bucket while still distinguishing
# real design steps (1 nm) well below human-meaningful resolution.
CIRCLE_RADIUS_KEY_DIGITS = 4
# Canonical density (point count) every non-CIRCLE template + candidate gets
# resampled to before centroid / PCA / scale / Chamfer. Matches the
# upper-bound density `collect_entity_points` uses for synthesised circles,
# keeping internal cloud sizes uniform between the two code paths. The cost
# is one searchsorted per candidate; the payoff is that 11-vertex and
# 65-vertex copies of the same physical substrate become 64-point clouds of
# identical density and Chamfer correctly to ~0 distance.
RESAMPLE_N = 64

# Worker count default. Single-process when 1 (no multiprocessing overhead).
# Set higher for very large drawings where the per-candidate alignment loop
# becomes the bottleneck. Lazy pool — only spun up on first n_jobs>1 call,
# then kept alive for module lifetime to amortise spawn cost.
N_JOBS = int(os.environ.get("SMDR2_N_JOBS", "1"))
_MIN_ITEMS_PER_WORKER = 200     # below this the pool overhead dominates


@dataclass
class EntityShape:
    handle: str
    points: np.ndarray            # (N, 2)
    centroid: np.ndarray          # (2,)
    radius: float                 # max distance from centroid (compact bound)
    path_length: float
    vertex_count: int
    kind: str | None = None       # source primitive type when uniform, else None
    # Singular values of the centered cloud's covariance, σ₁ ≥ σ₂ ≥ 0.
    # Rotation-/mirror-/translation-invariant; together they encode the
    # principal-axis aspect ratio (σ₂/σ₁) used by `signatures_compatible`.
    # Both are 0 for degenerate clouds (< 2 vertices or coincident points).
    pca_sigma1: float = 0.0
    pca_sigma2: float = 0.0

    @classmethod
    def from_points(
        cls,
        handle: str,
        points: list[tuple[float, float]],
        *,
        kind: str | None = None,
    ) -> "EntityShape":
        arr = np.asarray(points, dtype=np.float64)
        if arr.shape[0] == 0:
            return cls(handle, arr, np.zeros(2), 0.0, 0.0, 0, kind, 0.0, 0.0)
        # Compute path length on the raw sequence (includes the closing
        # segment for closed polylines) BEFORE dedup.
        path_length = (
            float(np.linalg.norm(np.diff(arr, axis=0), axis=1).sum())
            if len(arr) > 1 else 0.0
        )
        # Drop a trailing duplicate of the first point — common in
        # flattened closed polylines. Without this, the centroid and PCA
        # are pulled toward the duplicate, ruining mirror/rotation alignment.
        # Use absolute tolerance only — `np.allclose`'s default `rtol=1e-5`
        # scales with coordinate magnitude, so at 10⁵-mm world coords any
        # two points within ~1 mm get flagged as duplicates. For a real
        # circle template that drops the last vertex, shifts the centroid,
        # and inflates the recomputed radius — the user-reported scan-all
        # bug at far-from-origin DXFs.
        if arr.shape[0] >= 2 and np.allclose(arr[0], arr[-1], rtol=0, atol=1e-9):
            arr = arr[:-1]
        centroid = arr.mean(axis=0)
        d = arr - centroid
        radius = float(np.linalg.norm(d, axis=1).max())
        s1, s2 = _pca_singular_values(d)
        return cls(handle, arr, centroid, radius, path_length, arr.shape[0],
                   kind, s1, s2)

    @classmethod
    def from_circle(
        cls,
        handle: str,
        cx: float,
        cy: float,
        r: float,
    ) -> "EntityShape":
        """Fast-path constructor for CIRCLE primitives.

        Synthesises the same n-vertex point cloud `collect_entity_points`
        would emit (so legacy generic-chamfer matching against circle
        candidates still works), but fills `centroid` / `radius` /
        `path_length` / PCA sigmas analytically — skipping the heavy
        `_pca_singular_values` eigendecomp + `numpy.mean` / `linalg.norm`
        scans inside `from_points`. For a circle-heavy drawing (BGA grid
        with ~20k balls) this trims `build_entity_shapes` from ~1.6 s
        to a few hundred ms while keeping every downstream value
        numerically identical (verified to ≤ 1e-13).
        """
        import math
        n = max(8, min(64, round(2.0 * math.pi * r / 0.01)))
        angles = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
        points = np.column_stack((
            cx + r * np.cos(angles),
            cy + r * np.sin(angles),
        ))
        chord = 2.0 * r * math.sin(math.pi / n)
        # `from_points` computes path_length on the raw point sequence
        # before deduping; for an unclosed n-vertex polyline that's
        # (n-1) chord lengths, not n.
        path_length = float((n - 1) * chord)
        # PCA on a uniformly-sampled circle of radius r yields
        # σ₁ = σ₂ = r · √(n/2) exactly (cov = (n/2)·r² · I).
        sigma = float(r * math.sqrt(n / 2.0))
        return cls(
            handle=handle,
            points=points,
            centroid=np.array([float(cx), float(cy)], dtype=np.float64),
            radius=float(r),
            path_length=path_length,
            vertex_count=n,
            kind="circle",
            pca_sigma1=sigma,
            pca_sigma2=sigma,
        )


@dataclass
class MatchResult:
    handles: list[str]
    score: float                  # chamfer distance under best transform
    scale: float


@dataclass
class NearMiss:
    """A candidate that passed the cheap pre-filters but failed alignment.

    Useful for debugging "I thought this should match — why didn't it?":
        - reason="scale" → optimal scale fell outside [SCALE_MIN, SCALE_MAX]
        - reason="shape" → scale OK, but chamfer distance exceeded tolerance
    """
    handles: list[str]
    score: float                  # chamfer (or 0 if not computed)
    scale: float                  # optimal scale (or 0)
    reason: str                   # "scale" | "shape"


@dataclass
class MatchOutput:
    matches: list[MatchResult]
    near_misses: list[NearMiss]


# ---- Building drawing entity shapes ----------------------------------------
def build_entity_shapes(
    primitives: list[dict],
    handle_index: dict[str, list[int]],
) -> dict[str, EntityShape]:
    """One EntityShape per DXF handle, aggregating all its primitives' points.

    CIRCLE fast path: a handle backed by a single CIRCLE primitive skips
    the 32-vertex point synthesis + centroid/PCA computation and goes
    through `EntityShape.from_circle` directly. Single-CIRCLE matching
    only reads `kind` + `radius`; multi-entity matching never includes
    CIRCLE entities per the data contract. For circle-heavy drawings
    (BGA grids) this is the difference between ~1.6 s and a few hundred
    ms on the cold `_cached_shapes` build.
    """
    from app.library import collect_entity_kinds, collect_entity_points
    shapes: dict[str, EntityShape] = {}
    for h, pi_list in handle_index.items():
        if len(pi_list) == 1:
            p = primitives[pi_list[0]]
            if p["type"] == "circle":
                cx, cy = p["center"]
                shapes[h] = EntityShape.from_circle(h, cx, cy, float(p["r"]))
                continue
        pts = collect_entity_points(primitives, handle_index, h)
        if not pts:
            continue
        kind = collect_entity_kinds(primitives, handle_index, h)
        shapes[h] = EntityShape.from_points(h, pts, kind=kind)
    return shapes


# ---- PCA / alignment ------------------------------------------------------
def _pca_singular_values(centered: np.ndarray) -> tuple[float, float]:
    """Return (σ₁, σ₂) — sorted descending — for a centered 2D point cloud.

    Both values are 0 for degenerate inputs (< 2 rows or zero covariance).
    Kept independent of `_pca_axes` so `EntityShape.from_points` doesn't pay
    for the eigenvector copy when only the σ values are needed.
    """
    if centered.shape[0] < 2:
        return 0.0, 0.0
    cov = centered.T @ centered
    w = np.linalg.eigvalsh(cov)
    w = np.clip(w, 0.0, None)
    s = np.sqrt(w)
    s_sorted = np.sort(s)[::-1]
    return float(s_sorted[0]), float(s_sorted[1])


def _sigma_ratio(shape: "EntityShape") -> float:
    """σ₂/σ₁ ∈ [0, 1] — 0 for thin lines, 1 for isotropic clouds. Falls back
    to 0 when σ₁ == 0 (degenerate)."""
    if shape.pca_sigma1 <= 0.0:
        return 0.0
    return shape.pca_sigma2 / shape.pca_sigma1


def _pca_axes(centered: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (axes, singular_values) — axes is 2x2 rows = principal directions.

    Falls back to identity if the cloud is degenerate.
    """
    if centered.shape[0] < 2:
        return np.eye(2), np.zeros(2)
    cov = centered.T @ centered
    w, v = np.linalg.eigh(cov)
    order = np.argsort(w)[::-1]
    axes = v[:, order].T              # rows = principal axes
    sv = np.sqrt(np.maximum(w[order], 0.0))
    # Enforce right-handed (det = +1).
    if np.linalg.det(axes) < 0:
        axes[1] *= -1
    return axes, sv


def _try_alignments(
    template_centered: np.ndarray,
    candidate_centered: np.ndarray,
) -> Iterable[tuple[np.ndarray, str]]:
    """Yield candidate orientations of `candidate_centered` in template frame.

    We align candidate's principal axes to template's, then enumerate the 4
    rotation/mirror variants that remain after PCA (PCA is only canonical up
    to axis sign).
    """
    tpl_axes, _ = _pca_axes(template_centered)
    cand_axes, _ = _pca_axes(candidate_centered)
    # R such that cand_axes @ R = tpl_axes  =>  R = cand_axes.T @ tpl_axes
    R0 = cand_axes.T @ tpl_axes
    # Express candidate in template frame: pts @ R0
    base = candidate_centered @ R0
    # Variants: flip x, flip y, flip both, identity.
    diag_pp = np.diag([1.0, 1.0])
    diag_pm = np.diag([1.0, -1.0])
    diag_mp = np.diag([-1.0, 1.0])
    diag_mm = np.diag([-1.0, -1.0])
    yield base @ diag_pp, "++"
    yield base @ diag_pm, "+-"
    yield base @ diag_mp, "-+"
    yield base @ diag_mm, "--"


def _chamfer(
    a: np.ndarray,
    a_tree: cKDTree,
    b: np.ndarray,
    b_tree: cKDTree,
) -> float:
    """Symmetric mean chamfer distance between two point sets."""
    dab, _ = a_tree.query(b, k=1)
    dba, _ = b_tree.query(a, k=1)
    return float(0.5 * (dab.mean() + dba.mean()))


def _chamfer_brute(a: np.ndarray, b: np.ndarray) -> float:
    """Symmetric mean chamfer via O(N*M) numpy — faster than KDTree at small N.

    Cutover at ~50 points where tree construction overhead dominates.
    """
    # (N, M, 2) → (N, M) squared distances → row/col mins → sqrt → mean
    diffs = a[:, None, :] - b[None, :, :]
    d2 = (diffs * diffs).sum(axis=2)
    return float(0.5 * (np.sqrt(d2.min(axis=1)).mean()
                        + np.sqrt(d2.min(axis=0)).mean()))


BRUTE_FORCE_CUTOFF = 50


# ---- Lazy module-level worker pool ---------------------------------------
_match_pool: ProcessPoolExecutor | None = None
_match_pool_workers = 0


def _get_match_pool(n_workers: int) -> ProcessPoolExecutor:
    """Cache a process pool keyed by worker count. Spawn cost is paid once,
    not per request."""
    global _match_pool, _match_pool_workers
    if _match_pool is None or _match_pool_workers != n_workers:
        if _match_pool is not None:
            _match_pool.shutdown(wait=False)
        _match_pool = ProcessPoolExecutor(max_workers=n_workers)
        _match_pool_workers = n_workers
    return _match_pool


def shutdown_pool() -> None:
    global _match_pool
    if _match_pool is not None:
        _match_pool.shutdown(wait=False)
        _match_pool = None


def _dedup_closing(pts: np.ndarray) -> np.ndarray:
    """Drop the trailing-equals-first row that flattened closed polylines
    leave behind (centroid + PCA bias otherwise)."""
    if pts.shape[0] >= 2 and np.allclose(pts[0], pts[-1]):
        return pts[:-1]
    return pts


def _resample_arclength(points: np.ndarray, n: int) -> np.ndarray:
    """Return n points evenly spaced by cumulative arclength along `points`.

    Always traverses the polyline as if it were closed (last → first
    segment included). For genuinely-closed inputs (rectangles, BGA pads,
    substrate outlines) this is exactly what we want, and since
    `EntityShape.points` is stored post-dedup we have to add the closing
    segment back ourselves. For genuinely-open inputs (e.g. an isolated
    line) the artificial closing chord is applied identically to template
    and candidate so the bias cancels out under Chamfer.

    For degenerate inputs (< 2 vertices, or coincident vertices yielding
    zero total length) the cloud is returned unmodified or repeated;
    callers reject these via the `c_norm < 1e-9` guard.
    """
    if points.shape[0] < 2:
        return points
    extended = np.concatenate([points, points[:1]], axis=0)
    deltas = np.diff(extended, axis=0)
    seg = np.linalg.norm(deltas, axis=1)
    total = float(seg.sum())
    if total < 1e-12:
        return np.tile(points[:1], (n, 1))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    targets = np.linspace(0.0, total, n, endpoint=False)
    idx = np.clip(np.searchsorted(cum, targets, side="right") - 1,
                  0, len(seg) - 1)
    frac = (targets - cum[idx]) / seg[idx]
    return extended[idx] + frac[:, None] * deltas[idx]


def _canonical_start(points: np.ndarray) -> int:
    """Index of the vertex furthest from the centroid — a rotation- and
    winding-stable anchor for arclength resampling.

    `_resample_arclength` lays its N samples starting from `points[0]` in
    stored order, so two geometrically identical outlines stored with a
    different first vertex or opposite winding (CW vs CCW — common after a
    CAD copy / mirror / rotate-paste) resample to *misaligned* sample grids.
    At sharp corners that misalignment inflates the symmetric Chamfer well
    past `TOLERANCE_ABS`, so identical shapes register as `reason="shape"`
    near-misses. Anchoring the resample start at a geometry-determined vertex
    (furthest from the centroid — always a corner for a substrate/component
    outline) makes the sampling phase independent of vertex order and winding,
    so identical shapes resample to the *same* grid and score Chamfer ~0. Any
    residual corner-tie ambiguity (e.g. a near-square outline whose four
    corners are equidistant) is absorbed by the existing 4 sign-variant
    orientations downstream.
    """
    if points.shape[0] < 2:
        return 0
    c = points.mean(axis=0)
    return int(np.argmax(((points - c) ** 2).sum(axis=1)))


def _resample_canonical(points: np.ndarray, n: int) -> np.ndarray:
    """`_resample_arclength` with a phase/winding-invariant start vertex."""
    if points.shape[0] < 2:
        return points
    i = _canonical_start(points)
    rolled = points if i == 0 else np.roll(points, -i, axis=0)
    return _resample_arclength(rolled, n)


def align_score(
    template_pts: np.ndarray,
    candidate_pts: np.ndarray,
) -> tuple[float, float] | None:
    """Best (score, scale) over 4 PCA orientations, after optimal isotropic
    scaling. Returns None if scale falls outside [SCALE_MIN, SCALE_MAX].
    """
    if template_pts.shape[0] < 2 or candidate_pts.shape[0] < 2:
        return None
    # Resample both to canonical density so vertex-count differences don't
    # bias scale or Chamfer. _resample_canonical also anchors the sampling
    # phase to a geometry-stable start vertex so a different stored winding /
    # first vertex doesn't misalign the grids (see _canonical_start).
    template_pts = _resample_canonical(template_pts, RESAMPLE_N)
    candidate_pts = _resample_canonical(candidate_pts, RESAMPLE_N)
    if template_pts.shape[0] < 2 or candidate_pts.shape[0] < 2:
        return None
    t_centered = template_pts - template_pts.mean(axis=0)
    c_centered = candidate_pts - candidate_pts.mean(axis=0)

    t_norm = np.linalg.norm(t_centered, axis=1).mean()
    c_norm = np.linalg.norm(c_centered, axis=1).mean()
    if t_norm < 1e-9 or c_norm < 1e-9:
        return None
    scale = t_norm / c_norm
    if scale < SCALE_MIN or scale > SCALE_MAX:
        return None
    c_scaled = c_centered * scale

    t_tree = cKDTree(t_centered)
    best = None
    for cand_oriented, _ in _try_alignments(t_centered, c_scaled):
        c_tree = cKDTree(cand_oriented)
        d = _chamfer(t_centered, t_tree, cand_oriented, c_tree)
        if best is None or d < best:
            best = d
    return float(best), float(scale)


# Default bbox_ratio that signature-mode classes get when the user enables
# the strategy without naming a value. Tighter than the global pre-filter
# ratios (0.20) because chamfer is no longer downstream to catch wrong
# shapes — the dimensional gate IS the verdict.
SIGNATURE_DEFAULT_BBOX_RATIO = 0.05


# ---- Signature pre-filter -------------------------------------------------
def signatures_compatible(
    a: EntityShape,
    b: EntityShape,
    *,
    path_length_ratio: float | None = None,
    radius_ratio: float | None = None,
) -> bool:
    """Cheap dimensional + aspect agreement test. Returns True when `a` and
    `b` are within size and aspect tolerance.

    The two dimensional gates accept per-call overrides so signature-mode
    classes can tighten them (e.g., 0.05) without changing the global
    defaults that chamfer-mode classes rely on as a pre-filter. The
    σ-ratio aspect gate is intentionally not overridable here — its
    discrimination is qualitative (rect vs square, thin vs blob), not
    size-tolerant.

    Defaults resolve to the module-level `PATH_LENGTH_RATIO` / `RADIUS_RATIO`
    at call time (not at def time), so dev-mode overrides flow through.
    """
    if path_length_ratio is None:
        path_length_ratio = PATH_LENGTH_RATIO
    if radius_ratio is None:
        radius_ratio = RADIUS_RATIO
    # vertex_count is no longer a gate: same-shape entities with very
    # different vertex counts (e.g. mirrored substrate stored as 11 vs 65
    # verts) are genuine matches now that the matcher resamples to a
    # canonical density before PCA / Chamfer. We still reject truly empty
    # clouds.
    if a.vertex_count < 2 or b.vertex_count < 2:
        return False
    # Path length — cheapest scalar, runs first.
    if a.path_length > 0 and b.path_length > 0:
        pl_ratio = a.path_length / b.path_length
        if pl_ratio < (1 - path_length_ratio) or pl_ratio > (1 + path_length_ratio):
            return False
    # Max-distance-from-centroid (rotation-invariant linear bound).
    if a.radius > 0 and b.radius > 0:
        r_ratio = a.radius / b.radius
        if r_ratio < (1 - radius_ratio) or r_ratio > (1 + radius_ratio):
            return False
    # Principal-axis aspect ratio σ₂/σ₁ — rotation-/mirror-/scale-invariant.
    # Discriminates rect-vs-square at equal perimeter (σ-ratio ≈0.5 vs ≈1.0)
    # and thin-line-vs-blob (≈0 vs ≈1) without rejecting any allowed
    # rotation/mirror/scale.
    if abs(_sigma_ratio(a) - _sigma_ratio(b)) > SIGMA_RATIO_TOL:
        return False
    return True


# ---- Top-level matching --------------------------------------------------
def find_matches(
    template_handles: list[str],
    drawing_shapes: dict[str, EntityShape],
    tolerance: float | None = None,
    n_jobs: int | None = None,
    *,
    strategy: str = "chamfer",
    bbox_ratio: float | None = None,
) -> MatchOutput:
    """Find all matches of `template_handles` (as defined inside drawing_shapes)
    elsewhere in the drawing. Returns both confirmed matches and near-misses
    (candidates that passed the cheap pre-filters but failed alignment).

    `strategy` selects the matching pipeline:
    - "chamfer" (default): existing pipeline — signature pre-filter, scale
      window, PCA-aligned chamfer ≤ tolerance.
    - "signature": signature gate alone, with `bbox_ratio` (or
      `SIGNATURE_DEFAULT_BBOX_RATIO`) replacing both `PATH_LENGTH_RATIO`
      and `RADIUS_RATIO` for that call. Single-entity templates only —
      multi-entity templates silently fall back to chamfer.
    """
    if tolerance is None:
        tolerance = TOLERANCE_ABS
    n_jobs = N_JOBS if n_jobs is None else n_jobs
    template_handle_set = set(template_handles)
    template_shapes = [drawing_shapes[h] for h in template_handles if h in drawing_shapes]
    if not template_shapes:
        return MatchOutput(matches=[], near_misses=[])

    if strategy == "signature" and len(template_shapes) == 1:
        eff = bbox_ratio if bbox_ratio is not None else SIGNATURE_DEFAULT_BBOX_RATIO
        return _match_signature_mode(
            template_shapes[0], drawing_shapes, template_handle_set, eff,
        )
    if strategy == "signature" and len(template_shapes) > 1:
        logger.info(
            "matcher: signature strategy requested for multi-entity template "
            "(n=%d); falling back to chamfer pipeline.", len(template_shapes),
        )

    if len(template_shapes) == 1:
        tpl = template_shapes[0]
        if tpl.kind == "circle" and tpl.radius > 0:
            return _match_single_circle(tpl, drawing_shapes, template_handle_set)
        return _match_single(tpl, drawing_shapes, template_handle_set,
                             tolerance, n_jobs=n_jobs)
    return _match_multi(
        template_shapes, drawing_shapes, template_handle_set, tolerance,
        exclude_template_positions=True,
    )


def _match_signature_mode(
    template: EntityShape,
    drawing: dict[str, EntityShape],
    skip: set[str],
    bbox_ratio: float,
) -> MatchOutput:
    """Single-entity signature-only matching.

    A candidate is a match iff `signatures_compatible(template, candidate,
    path_length_ratio=bbox_ratio, radius_ratio=bbox_ratio)` returns True.
    Returns score=0.0, scale=candidate.radius / template.radius. No
    near-misses are emitted under this mode.
    """
    matches: list[MatchResult] = []
    for handle, shape in drawing.items():
        if handle in skip:
            continue
        if not signatures_compatible(
            template, shape,
            path_length_ratio=bbox_ratio, radius_ratio=bbox_ratio,
        ):
            continue
        scale = (shape.radius / template.radius) if template.radius > 0 else 1.0
        matches.append(MatchResult(handles=[handle], score=0.0, scale=scale))
    return MatchOutput(matches=matches, near_misses=[])


def find_matches_from_pointsets(
    entity_point_sets: list[list[tuple[float, float]]],
    drawing_shapes: dict[str, EntityShape],
    tolerance: float | None = None,
    n_jobs: int | None = None,
    entity_kinds: list[str | None] | None = None,
    *,
    strategy: str = "chamfer",
    bbox_ratio: float | None = None,
) -> MatchOutput:
    """Match a Template (stored as per-entity point sets) against a drawing.

    Used by /api/scan-all where the template doesn't live in the drawing's
    handle space — we construct virtual EntityShape objects from the stored
    points and feed them straight into the matching pipeline.

    `entity_kinds` (parallel to `entity_point_sets`) is the primitive type
    that each point set was collected from at commit time, e.g. `"circle"`.
    When provided AND the template is a single entity with kind `"circle"`,
    we take the radius-bucket fast path. Legacy templates without kinds fall
    back to the generic pipeline.

    `strategy` / `bbox_ratio` mirror `find_matches`: signature-mode is
    available for single-entity templates only; multi-entity templates
    silently fall back to chamfer.
    """
    if tolerance is None:
        tolerance = TOLERANCE_ABS
    n_jobs = N_JOBS if n_jobs is None else n_jobs
    if entity_kinds is None:
        kinds_iter: list[str | None] = [None] * len(entity_point_sets)
    else:
        if len(entity_kinds) != len(entity_point_sets):
            raise ValueError("entity_kinds length must equal entity_point_sets length")
        kinds_iter = list(entity_kinds)
    template_shapes: list[EntityShape] = [
        EntityShape.from_points(f"_template_{i}", pts, kind=k)
        for i, (pts, k) in enumerate(zip(entity_point_sets, kinds_iter))
        if pts
    ]
    if not template_shapes:
        return MatchOutput(matches=[], near_misses=[])
    skip: set[str] = set()
    if strategy == "signature" and len(template_shapes) == 1:
        eff = bbox_ratio if bbox_ratio is not None else SIGNATURE_DEFAULT_BBOX_RATIO
        return _match_signature_mode(
            template_shapes[0], drawing_shapes, skip, eff,
        )
    if strategy == "signature" and len(template_shapes) > 1:
        logger.info(
            "matcher: signature strategy requested for multi-entity template "
            "(n=%d); falling back to chamfer pipeline.", len(template_shapes),
        )
    if len(template_shapes) == 1:
        tpl = template_shapes[0]
        if tpl.kind == "circle" and tpl.radius > 0:
            return _match_single_circle(tpl, drawing_shapes, skip)
        return _match_single(tpl, drawing_shapes, skip, tolerance, n_jobs=n_jobs)
    # Library scan: the saved template doesn't live in this drawing's
    # handle space, so "don't match against template's own physical
    # position" makes no sense — the original instance the template was
    # saved from is a legitimate match. (In-drawing `find_matches`
    # exclude_template_positions=True because the user explicitly
    # picked those handles and wants to find OTHERS.)
    return _match_multi(
        template_shapes, drawing_shapes, skip, tolerance,
        exclude_template_positions=False,
    )


# ---- Single-CIRCLE fast path --------------------------------------------
def _radius_bucket_key(r: float) -> int:
    """Bucket key for the exact-radius CIRCLE fast path.

    Integer return so bit-identical floats hash identically and floats within
    10⁻¹⁰ round to the same bucket. See design.md for the rationale.
    """
    return round(r * (10 ** CIRCLE_RADIUS_KEY_DIGITS))


# Cache the bucket dict per drawing identity. `_shapes_for(file_id)` keeps
# each file's drawing dict alive until invalidation (library swap, re-
# preprocess), at which point a fresh dict object is produced and gets a
# fresh `id()` → fresh cache slot.
_radius_bucket_cache: dict[int, dict[int, list[str]]] = {}


def _get_radius_buckets(
    drawing: dict[str, EntityShape],
) -> dict[int, list[str]]:
    """Return (and cache) the per-drawing radius bucket dict."""
    cache_id = id(drawing)
    cached = _radius_bucket_cache.get(cache_id)
    if cached is not None:
        return cached
    buckets: dict[int, list[str]] = {}
    for h, s in drawing.items():
        if s.kind != "circle" or s.radius <= 0:
            continue
        buckets.setdefault(_radius_bucket_key(s.radius), []).append(h)
    _radius_bucket_cache[cache_id] = buckets
    return buckets


# ---- Multi-entity fingerprint bucket -----------------------------------
# Decimal digits kept when bucketing the (path_length, radius, sigma_ratio)
# triple that fingerprints each EntityShape for the rigid-transform
# multi-entity matcher. 1e-3 mm (= 1 µm) bucket grid, paired with the
# ±1-neighbour bucket lookup below, lets visually-identical copies with
# up to ~1 µm of per-coord noise cluster while still distinguishing
# real packaging-design steps (which are 5+ µm apart).
#
# Was 6 originally on the theory that block-insert FP noise sits at
# ULP-scale (1e-13), but real CAD pipelines emit copies whose coordinates
# differ at the µm scale (manually-placed instances, mixed-precision
# transforms, round-tripped through ASCII DXF). At digit 6 a single µm
# of perimeter drift puts each visually-identical instance in its own
# singleton bucket — breaking the bucket-lookup contract that powers
# _match_multi.
FINGERPRINT_DIGITS = 3

# Fingerprint bucket lookup radius (in fp units = 10^-FINGERPRINT_DIGITS mm).
# `_match_multi` enumerates buckets at fp ± this many bins in each of the
# three dims (3^3 = 27 buckets per lookup), and the per-"other" verification
# accepts any candidate whose fp lies within this same ±1 box. Buys
# robustness against noise that straddles a bucket boundary at negligible
# cost.
FINGERPRINT_NEIGHBOR = 1

# Centroid-distance tolerance for the rigid-transform "is this entity at
# the predicted spot" check. Sized to absorb PCA-driven prediction drift
# when the seed entity's vertices have µm-scale noise. Was 1e-6 originally
# (sized for bit-identical synthetic data), but rejected real-world
# bit-similar-but-not-identical copies. False-match risk is negligible:
# the fingerprint neighbour check is the real shape gate, and entity
# centroids in packaging designs are mm-scale apart — orders of magnitude
# above this tolerance.
CENTROID_NOISE_TOL = 1e-3


def _fingerprint(shape: EntityShape) -> tuple[int, int, int]:
    """Translation-/rotation-/mirror-invariant identity key for an
    `EntityShape` under the rigid-transform multi-match model.

    Returns a triple of ints (multiply-then-round) so two visually
    identical shapes — even when stored with different vertex orderings
    or starting points — hash to the same bucket. Floats would risk dict
    keys not comparing equal across copies due to ULP-level noise.
    """
    sigma_ratio = _sigma_ratio(shape)
    mult = 10 ** FINGERPRINT_DIGITS
    return (
        round(shape.path_length * mult),
        round(shape.radius * mult),
        round(sigma_ratio * mult),
    )


def _fingerprint_neighbours(
    fp: tuple[int, int, int], radius: int = FINGERPRINT_NEIGHBOR,
) -> Iterable[tuple[int, int, int]]:
    """Yield every fingerprint within ±radius of fp in each dim.

    Used to absorb instances whose noise nudges them across a bucket
    boundary. For radius=1 this is the 27-cell box around fp.
    """
    p0, p1, p2 = fp
    for dp in range(-radius, radius + 1):
        for dr in range(-radius, radius + 1):
            for ds in range(-radius, radius + 1):
                yield (p0 + dp, p1 + dr, p2 + ds)


def _fingerprint_matches(
    a: tuple[int, int, int], b: tuple[int, int, int],
    *, radius: int = FINGERPRINT_NEIGHBOR,
) -> bool:
    """True iff each component of `a` and `b` differ by ≤ radius."""
    return (
        abs(a[0] - b[0]) <= radius
        and abs(a[1] - b[1]) <= radius
        and abs(a[2] - b[2]) <= radius
    )


# Same lifetime / invalidation contract as `_radius_bucket_cache` — bound
# to drawing dict identity, replaced when `_shapes_for(file_id)` produces
# a fresh dict (library swap, re-preprocess).
_fingerprint_bucket_cache: dict[int, dict[tuple[int, int, int], list[str]]] = {}


def _cluster_key(shape: EntityShape) -> tuple[int, int, tuple[int, int, int]]:
    """Identity key for "this entity is a stacked duplicate of that one".

    Two entities at the same centroid (within `CENTROID_NOISE_TOL`) with
    the same fingerprint collapse to the same key. Used by
    `_get_position_clusters` so the multi-entity matcher can (a) dedupe
    the user's template selection — frame-select grabs every overlapping
    polyline, click-select grabs one, and the matcher needs them to
    behave identically — and (b) expand each matched handle back into
    the full set of overlapping drawing handles for highlighting.
    """
    cx, cy = float(shape.centroid[0]), float(shape.centroid[1])
    grid = 1.0 / CENTROID_NOISE_TOL
    return (round(cx * grid), round(cy * grid), _fingerprint(shape))


_position_cluster_cache: dict[int, dict[tuple[int, int, tuple[int, int, int]], list[str]]] = {}


def _get_position_clusters(
    drawing: dict[str, EntityShape],
) -> dict[tuple[int, int, tuple[int, int, int]], list[str]]:
    """Cluster drawing handles by `_cluster_key`. Cached per drawing dict
    identity, same contract as `_radius_bucket_cache`."""
    cache_id = id(drawing)
    cached = _position_cluster_cache.get(cache_id)
    if cached is not None:
        return cached
    clusters: dict[tuple[int, int, tuple[int, int, int]], list[str]] = {}
    for h, s in drawing.items():
        clusters.setdefault(_cluster_key(s), []).append(h)
    _position_cluster_cache[cache_id] = clusters
    return clusters


def _dedupe_template_shapes(
    template_shapes: list[EntityShape],
) -> list[EntityShape]:
    """Drop entities sharing a cluster key with one already kept.

    Order-preserving: returns the first occurrence of each cluster so
    `find_matches`' template-handle semantics (seed = first selected
    handle) stay deterministic.
    """
    seen: set[tuple[int, int, tuple[int, int, int]]] = set()
    unique: list[EntityShape] = []
    for t in template_shapes:
        key = _cluster_key(t)
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)
    return unique


def _get_fingerprint_buckets(
    drawing: dict[str, EntityShape],
) -> dict[tuple[int, int, int], list[str]]:
    """Return (and cache) the per-drawing fingerprint bucket dict.

    Bucket index used by `_match_multi` to enumerate candidate seeds in
    O(bucket_size) instead of scanning the whole drawing with a
    signatures gate.

    CIRCLE entities are skipped — multi-entity templates never include
    CIRCLE entities per the data contract (BGA balls / fiducial circles
    are always single-entity matches via `_match_single_circle`). Also
    the CIRCLE fast path leaves `path_length` and `sigma_ratio` at
    defaults, so a circle's `_fingerprint` is meaningless and would
    accidentally collide with degenerate non-circle entities.
    """
    cache_id = id(drawing)
    cached = _fingerprint_bucket_cache.get(cache_id)
    if cached is not None:
        return cached
    buckets: dict[tuple[int, int, int], list[str]] = {}
    for h, s in drawing.items():
        if s.kind == "circle":
            continue
        buckets.setdefault(_fingerprint(s), []).append(h)
    _fingerprint_bucket_cache[cache_id] = buckets
    return buckets


def _match_single_circle(
    template: EntityShape,
    drawing: dict[str, EntityShape],
    skip: set[str],
) -> MatchOutput:
    """Exact-radius bucket lookup for single-CIRCLE templates.

    Bypasses PCA + Chamfer entirely. No NearMiss emitted: circle similarity
    is a single number the user reads off the canvas, and emitting an object
    per off-bucket entity was the dominant cost in the prior generic path.
    """
    key = _radius_bucket_key(template.radius)
    buckets = _get_radius_buckets(drawing)
    # ±1 neighbour lookup absorbs banker's-rounding fence-post drift: the
    # drawing side uses `from_circle`'s analytical r; library-stored templates
    # reload through `from_points` which recomputes r numerically. When r·10⁴
    # lands on a .5 boundary, ULP-scale drift flips the bucket.
    hits: list[str] = []
    for k in (key - 1, key, key + 1):
        hits.extend(buckets.get(k, []))
    matches = [
        MatchResult(handles=[h], score=0.0, scale=1.0)
        for h in hits if h not in skip
    ]
    return MatchOutput(matches=matches, near_misses=[])


SIGN_VARIANTS = (
    np.array([1.0, 1.0]),
    np.array([1.0, -1.0]),
    np.array([-1.0, 1.0]),
    np.array([-1.0, -1.0]),
)


def _match_single(
    template: EntityShape,
    drawing: dict[str, EntityShape],
    skip: set[str],
    tolerance: float,
    n_jobs: int = 1,
) -> MatchOutput:
    """Single-entity match dispatcher — serial or fan out across processes."""
    if n_jobs <= 1:
        return _match_single_serial(template, drawing, skip, tolerance)
    items = [(h, s) for h, s in drawing.items() if h not in skip]
    if len(items) < n_jobs * _MIN_ITEMS_PER_WORKER:
        return _match_single_serial(template, drawing, skip, tolerance)
    chunk_size = (len(items) + n_jobs - 1) // n_jobs
    chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
    pool = _get_match_pool(n_jobs)
    futs = [pool.submit(_match_single_chunk, template, chunk, tolerance)
            for chunk in chunks]
    matches: list[MatchResult] = []
    near: list[NearMiss] = []
    for fut in futs:
        out = fut.result()
        matches.extend(out.matches)
        near.extend(out.near_misses)
    return MatchOutput(matches=matches, near_misses=near)


def _match_single_chunk(
    template: EntityShape,
    chunk: list[tuple[str, EntityShape]],
    tolerance: float,
) -> MatchOutput:
    """Pickleable worker — runs the serial match over a candidate sublist."""
    return _match_single_serial(template, dict(chunk), set(), tolerance)


def _match_single_serial(
    template: EntityShape,
    drawing: dict[str, EntityShape],
    skip: set[str],
    tolerance: float,
) -> MatchOutput:
    """Single-entity match — hot loop, heavily inlined for speed.

    Template-side state (resampled points, centered, PCA axes, mean radius)
    is computed once outside the loop instead of once per candidate. Both
    template and candidate clouds are resampled to RESAMPLE_N points along
    their arclength so per-entity vertex-count differences don't bias scale
    or Chamfer (see `_resample_arclength` + design.md in the
    improve-polyline-density-invariance change).
    """
    matches: list[MatchResult] = []
    near: list[NearMiss] = []

    if template.vertex_count < 2:
        return MatchOutput(matches=matches, near_misses=near)

    # Template-side once. Canonical resampling anchors the sampling phase to a
    # geometry-stable start vertex so candidates stored with a different first
    # vertex / winding still align (see _canonical_start).
    t_resampled = _resample_canonical(template.points, RESAMPLE_N)
    t_centered = t_resampled - t_resampled.mean(axis=0)
    t_axes, _ = _pca_axes(t_centered)
    t_norm = float(np.linalg.norm(t_centered, axis=1).mean())
    if t_norm < 1e-9:
        return MatchOutput(matches=matches, near_misses=near)
    # Cloud size is now uniform per call, so the brute-vs-tree branch is a
    # one-shot decision outside the loop.
    use_tree = t_centered.shape[0] > BRUTE_FORCE_CUTOFF
    t_tree = cKDTree(t_centered) if use_tree else None

    for handle, shape in drawing.items():
        if handle in skip:
            continue
        if not signatures_compatible(template, shape):
            continue
        if shape.points.shape[0] < 2:
            continue

        c_resampled = _resample_canonical(shape.points, RESAMPLE_N)
        c_centered = c_resampled - c_resampled.mean(axis=0)
        c_norm = float(np.linalg.norm(c_centered, axis=1).mean())
        if c_norm < 1e-9:
            continue
        scale = t_norm / c_norm
        if scale < SCALE_MIN or scale > SCALE_MAX:
            near.append(NearMiss(handles=[handle], score=0.0, scale=scale, reason="scale"))
            continue

        c_scaled = c_centered * scale
        c_axes, _ = _pca_axes(c_scaled)
        # R0 maps candidate PCA frame to template PCA frame.
        base = c_scaled @ (c_axes.T @ t_axes)

        best = float("inf")
        for sign in SIGN_VARIANTS:
            cand = base * sign  # broadcast
            if use_tree:
                c_tree = cKDTree(cand)
                d = _chamfer(t_centered, t_tree, cand, c_tree)
            else:
                d = _chamfer_brute(t_centered, cand)
            if d < best:
                best = d
                if best <= tolerance:
                    break  # already good enough, no need to try more variants

        if best <= tolerance:
            matches.append(MatchResult(handles=[handle], score=best, scale=scale))
        else:
            near.append(NearMiss(handles=[handle], score=best, scale=scale, reason="shape"))

    return MatchOutput(matches=matches, near_misses=near)


def _match_multi(
    template_shapes: list[EntityShape],
    drawing: dict[str, EntityShape],
    skip: set[str],
    tolerance: float,  # noqa: ARG001 — kept for signature parity; unused in rigid path
    *,
    exclude_template_positions: bool = True,
) -> MatchOutput:  # noqa: PLR0915  — single linear pipeline reads cleaner than fragmenting
    """Rigid-transform multi-entity matching.

    Assumes the drawing's named-object instances are translate / rotate /
    mirror copies of the template — no scale, no shape drift. Pipeline:

      1. Pick the seed: rarest template entity by drawing-side bucket size
         (`_get_fingerprint_buckets`).
      2. Encode every other template entity's centroid in the seed's
         PCA-local frame.
      3. Enumerate candidate seeds from `buckets[fingerprint(seed)]` only
         — no full-drawing scan, no `signatures_compatible` filter.
      4. For each candidate seed, derive the rigid transform (R, t) by
         aligning the seed template's PCA axes to the candidate's,
         enumerating 4 sign variants for mirror / 180° ambiguity.
      5. For each other template entity, predict its world centroid via
         (R, t); look up the nearest drawing centroid in the KDTree.
         Accept only when the distance is within `CENTROID_NOISE_TOL`
         AND the fingerprint matches.
      6. A match is reported when every other template entity is found.

    No chamfer / `align_score` calls. Match `score` is always 0.0 and
    `scale` always 1.0 — the rigid contract has no degrees of freedom
    for either to deviate.

    Limitation: square / isotropic seeds (σ-ratio ≈ 1) make PCA axes
    rotationally ambiguous beyond the 4-sign variants — the matcher may
    miss rotated copies in that case. Seed selection picks the rarest
    template entity by bucket size, which usually avoids isotropic
    candidates if the template has any non-isotropic entity. If the
    entire template is isotropic, splitting it differently is the
    workaround.
    """
    # (a) Collapse stacked template entities so the rigid alignment
    # doesn't over-constrain itself (frame-select grabs 6 polylines for
    # a 3-rect SMD; click-select grabs 3; they must behave identically).
    if len(template_shapes) > 1:
        template_shapes = _dedupe_template_shapes(template_shapes)
        if len(template_shapes) == 1:
            return _match_single(template_shapes[0], drawing, skip, tolerance)

    # (b) Cluster index for two purposes downstream: (i) skipping cands
    # whose physical position coincides with the template's — that's a
    # stacked twin of the template, not a new match — and (ii) expanding
    # each match group back to its full set of overlapping handles for
    # highlighting. We must NOT extend `skip` to all cluster members:
    # close-packed neighbour SMDs whose pads touch share a cluster with
    # the template's pads, and extending would lock the neighbours out.
    position_clusters = _get_position_clusters(drawing)
    # Empty set when `exclude_template_positions` is False — the
    # cand-loop's membership check below then becomes a no-op,
    # letting drawing entities at the template's own physical
    # positions match. Library scan (`find_matches_from_pointsets`)
    # needs this so the original instance the template was saved
    # from is still found.
    template_cluster_keys: set[tuple[int, int, tuple[int, int, int]]] = (
        {_cluster_key(t) for t in template_shapes}
        if exclude_template_positions else set()
    )

    # (c) Per-role multiplicity = how many handles the user originally
    # selected at each template cluster. Frame-select = 2 per visual
    # rect; click-select = 1. Used to bound cluster expansion so every
    # match group ends up with the same handle count, even when a
    # neighbour SMD's shared pad cluster contains extra members.
    template_multiplicity: dict[tuple[int, int, tuple[int, int, int]], int] = {}
    for h in skip:
        if h not in drawing:
            continue
        key = _cluster_key(drawing[h])
        template_multiplicity[key] = template_multiplicity.get(key, 0) + 1

    handles = list(drawing.keys())
    centroids = np.stack([drawing[h].centroid for h in handles])
    tree = cKDTree(centroids)

    buckets = _get_fingerprint_buckets(drawing)

    def _bucket_size_with_neighbours(t: EntityShape) -> int:
        return sum(
            len(buckets.get(fp, []))
            for fp in _fingerprint_neighbours(_fingerprint(t))
        )

    # Rarest template entity = smallest fingerprint neighbourhood in the
    # drawing. Neighbour-bucket lookup absorbs noise that straddles
    # bucket boundaries (real CAD copies often differ by ~1 µm at the
    # coord level).
    seed = min(template_shapes, key=_bucket_size_with_neighbours)
    others = [t for t in template_shapes if t is not seed]
    other_fingerprints = [_fingerprint(t) for t in others]

    # Encode other template entities' positions in seed's PCA-local frame.
    seed_centered = seed.points - seed.centroid
    seed_axes, _ = _pca_axes(seed_centered)
    seed_key = _cluster_key(seed)
    others_local: list[tuple[EntityShape, np.ndarray, tuple[int, int, int], tuple[int, int, tuple[int, int, int]]]] = []
    for t, fp in zip(others, other_fingerprints):
        local = (t.centroid - seed.centroid) @ seed_axes.T
        others_local.append((t, local, fp, _cluster_key(t)))

    sign_variants = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

    # Raw matches before cluster expansion: each match is a list of
    # (drawing_handle, template_cluster_key) so expansion can bound by
    # per-role multiplicity later.
    raw_matches: list[list[tuple[str, tuple[int, int, tuple[int, int, int]]]]] = []
    near: list[NearMiss] = []
    seen_groups: set[tuple[str, ...]] = set()

    seed_fp = _fingerprint(seed)
    # Enumerate every handle in the ±1 fingerprint neighbourhood of the
    # seed (27 buckets at most). Pulls in copies whose own fingerprint
    # rounds to an adjacent cell due to per-vertex noise — without this
    # the seed bucket is a singleton for each visually-identical-but-
    # numerically-slightly-different instance.
    seed_bucket: list[str] = []
    seen_seed: set[str] = set()
    for fp in _fingerprint_neighbours(seed_fp):
        for h in buckets.get(fp, []):
            if h not in seen_seed:
                seen_seed.add(h)
                seed_bucket.append(h)

    seed_is_isotropic = _sigma_ratio(seed) > ISOTROPIC_SIGMA_RATIO_THRESHOLD

    if seed_is_isotropic:
        # ---- 2-point alignment fallback for isotropic seeds ----------
        # PCA principal-axis direction is numerically unstable for
        # circles, regular polygons, rounded squares (σ₂/σ₁ ≈ 1). The
        # 4 sign variants the anisotropic path uses only cover
        # 90°/mirror — not arbitrary rotation — so seed-driven pose
        # recovery misses copy-paste instances whose PCA happens to
        # spin to a random angle. Instead, derive the rigid transform
        # from the line between two centroid pairs: template
        # (seed→first-other) vs drawing (cand_seed→cand_other_first).
        # No PCA orientation needed.
        other_first_t, _, other_first_fp, other_first_key = others_local[0]
        template_vec = other_first_t.centroid - seed.centroid
        template_dist = float(np.linalg.norm(template_vec))
        if template_dist < CENTROID_NOISE_TOL:
            # Coincident template entities — fall back to anisotropic
            # path; nothing the 2-point alignment can do here.
            seed_is_isotropic = False

    if seed_is_isotropic:
        # Per-template orthonormal basis aligned with the seed→other_first
        # line. `(x_local, y_local)` of each remaining other-template
        # entity is its offset projected onto this basis.
        t_x = template_vec / template_dist
        t_y = np.array([-t_x[1], t_x[0]])  # 90° CCW perpendicular
        others_local_xy: list[tuple[
            float, float, tuple[int, int, int],
            tuple[int, int, tuple[int, int, int]],
        ]] = []
        for t, _local_pos_pca, t_fp, t_key in others_local[1:]:
            off = t.centroid - seed.centroid
            others_local_xy.append((
                float(off @ t_x), float(off @ t_y), t_fp, t_key,
            ))

        # First-other-entity bucket (same ±1 neighbour expansion as
        # seed enumeration; same `_fingerprint_neighbours` helper).
        other_first_bucket: list[str] = []
        seen_other_first: set[str] = set()
        for fp in _fingerprint_neighbours(other_first_fp):
            for h in buckets.get(fp, []):
                if h not in seen_other_first:
                    seen_other_first.add(h)
                    other_first_bucket.append(h)

        # N≥3 templates still need to cover mirror ambiguity (2 points
        # only constrain rotation; for 3+ the handedness matters).
        mirror_variants = (False,) if len(template_shapes) <= 2 else (False, True)

        for cand_seed_handle in seed_bucket:
            if cand_seed_handle in skip:
                continue
            if _cluster_key(drawing[cand_seed_handle]) in template_cluster_keys:
                continue
            cand_seed = drawing[cand_seed_handle]

            for cand_other_handle in other_first_bucket:
                if cand_other_handle == cand_seed_handle:
                    continue
                if cand_other_handle in skip:
                    continue
                if _cluster_key(drawing[cand_other_handle]) in template_cluster_keys:
                    continue
                cand_other = drawing[cand_other_handle]

                cand_vec = cand_other.centroid - cand_seed.centroid
                cand_dist = float(np.linalg.norm(cand_vec))
                if abs(cand_dist - template_dist) > CENTROID_NOISE_TOL:
                    continue  # distance gate — fast reject most pairs

                d_x = cand_vec / cand_dist
                d_y = np.array([-d_x[1], d_x[0]])

                for mirror in mirror_variants:
                    matched_iso: list[tuple[str, tuple[int, int, tuple[int, int, int]]]] = [
                        (cand_seed_handle, seed_key),
                        (cand_other_handle, other_first_key),
                    ]
                    matched_iso_handles: set[str] = {cand_seed_handle, cand_other_handle}
                    consistent_iso = True

                    for x_local, y_local, t_fp, t_key in others_local_xy:
                        y_eff = -y_local if mirror else y_local
                        expected = (
                            cand_seed.centroid
                            + x_local * d_x
                            + y_eff * d_y
                        )
                        nearby = tree.query_ball_point(expected, r=CENTROID_NOISE_TOL)
                        found_iso: str | None = None
                        for idx in nearby:
                            h = handles[idx]
                            if h in skip or h in matched_iso_handles:
                                continue
                            if not _fingerprint_matches(
                                _fingerprint(drawing[h]), t_fp,
                            ):
                                continue
                            found_iso = h
                            break
                        if found_iso is None:
                            consistent_iso = False
                            break
                        matched_iso.append((found_iso, t_key))
                        matched_iso_handles.add(found_iso)

                    if consistent_iso:
                        group = tuple(sorted(matched_iso_handles))
                        if group not in seen_groups:
                            seen_groups.add(group)
                            raw_matches.append(matched_iso)
                        break  # first successful mirror variant wins for this pair
    else:
        # ---- Anisotropic-seed path: PCA-driven 4-sign-variant rigid transform.
        # Unchanged from the original implementation.
        for cand_handle in seed_bucket:
            if cand_handle in skip:
                continue
            # A candidate whose cluster matches one of the template's
            # cluster keys sits at the template's exact physical position —
            # i.e. it's a stacked twin of the template that the user just
            # happened not to select. Re-matching against the template's
            # own position is not what the user asked for.
            if _cluster_key(drawing[cand_handle]) in template_cluster_keys:
                continue
            cand = drawing[cand_handle]

            # Candidate's PCA frame in world.
            cand_axes, _ = _pca_axes(cand.points - cand.centroid)

            for sx, sy in sign_variants:
                # 4 mirror/flip variants of how candidate's PCA aligns to template's.
                scaled_axes = cand_axes * np.array([[sx], [sy]])

                matched: list[tuple[str, tuple[int, int, tuple[int, int, int]]]] = [(cand_handle, seed_key)]
                matched_handle_set: set[str] = {cand_handle}
                consistent = True

                for t, local_pos, t_fp, t_key in others_local:
                    expected = local_pos @ scaled_axes + cand.centroid
                    # `query_ball_point` (not k=1) is essential when the
                    # drawing has stacked duplicates or neighbour SMDs whose
                    # pads coincide with the predicted spot — KDTree's k=1
                    # tiebreak returns the lowest-index handle, which may
                    # be in `skip` even when an acceptable cluster-mate is
                    # sitting at the same coordinate.
                    nearby = tree.query_ball_point(expected, r=CENTROID_NOISE_TOL)
                    found: str | None = None
                    for idx in nearby:
                        h = handles[idx]
                        if h in skip or h in matched_handle_set:
                            continue
                        if not _fingerprint_matches(
                            _fingerprint(drawing[h]), t_fp,
                        ):
                            continue
                        found = h
                        break
                    if found is None:
                        consistent = False
                        break
                    matched.append((found, t_key))
                    matched_handle_set.add(found)

                if consistent:
                    group = tuple(sorted(matched_handle_set))
                    if group not in seen_groups:
                        seen_groups.add(group)
                        raw_matches.append(matched)
                    break  # don't try other sign variants — first hit wins

    # Expand each match to its full position-cluster set, bounded by
    # `template_multiplicity` so every match group ends up with the
    # same handle count as the template — even when a shared-pad
    # cluster (close-packed neighbour SMDs) contains extra members
    # from another SMD's pad at the same coordinate.
    #
    # Two raw matches that occupy the same physical positions (i.e.
    # whose handles share cluster_keys) but picked different stacked
    # duplicates collapse to one — otherwise click-select on a
    # frame-stacked drawing would produce N copies of every match.
    expanded_seen_physical: set[tuple] = set()
    expanded: list[MatchResult] = []
    for matched in raw_matches:
        full: set[str] = set()
        for h, role_key in matched:
            n_target = template_multiplicity.get(role_key, 1)
            cluster_members = position_clusters.get(
                _cluster_key(drawing[h]), [h],
            )
            # Always include the matched handle itself (it's the one
            # the alignment actually verified), then top up to the
            # template's multiplicity with the lowest-index non-skip
            # cluster-mates so the count stays consistent across
            # match groups.
            picked: list[str] = [h]
            for cm in cluster_members:
                if len(picked) >= n_target:
                    break
                if cm == h or cm in picked or cm in skip:
                    continue
                picked.append(cm)
            full.update(picked)
        full -= skip
        if not full:
            continue
        physical_fp = tuple(sorted(
            _cluster_key(drawing[h]) for h in full
        ))
        if physical_fp in expanded_seen_physical:
            continue
        expanded_seen_physical.add(physical_fp)
        expanded.append(MatchResult(
            handles=sorted(full), score=0.0, scale=1.0,
        ))

    return MatchOutput(matches=expanded, near_misses=near)


# ---- Diagnostic: why does A-as-template find B but B-as-template not find A?
# Used by /api/files/{file_id}/match-swap. Dumps per-pair gate + alignment
# breakdown in both directions plus a focused trace of the multi-entity pose
# search, so a reported asymmetry can be pinpointed without re-instrumenting.
def _shape_summary(s: EntityShape) -> dict:
    return {
        "handle": s.handle,
        "kind": s.kind,
        "vertex_count": s.vertex_count,
        "path_length": s.path_length,
        "radius": s.radius,
        "pca_sigma1": s.pca_sigma1,
        "pca_sigma2": s.pca_sigma2,
        "sigma_ratio": _sigma_ratio(s),
        "centroid": [float(s.centroid[0]), float(s.centroid[1])],
    }


def _gate_detail(a: EntityShape, b: EntityShape) -> dict:
    """Per-gate breakdown of signatures_compatible(a, b). Asymmetric: a is the
    template side, b the candidate side."""
    out: dict = {"vertex_count_ok": a.vertex_count >= 2 and b.vertex_count >= 2}
    if a.path_length > 0 and b.path_length > 0:
        ratio = a.path_length / b.path_length
        out["path_length_ratio"] = ratio
        out["path_length_ok"] = (
            (1 - PATH_LENGTH_RATIO) <= ratio <= (1 + PATH_LENGTH_RATIO)
        )
    else:
        out["path_length_ratio"] = None
        out["path_length_ok"] = True
    if a.radius > 0 and b.radius > 0:
        r_ratio = a.radius / b.radius
        out["radius_ratio"] = r_ratio
        out["radius_ok"] = (1 - RADIUS_RATIO) <= r_ratio <= (1 + RADIUS_RATIO)
    else:
        out["radius_ratio"] = None
        out["radius_ok"] = True
    s_diff = abs(_sigma_ratio(a) - _sigma_ratio(b))
    out["sigma_ratio_diff"] = s_diff
    out["sigma_ratio_ok"] = s_diff <= SIGMA_RATIO_TOL
    out["compatible"] = signatures_compatible(a, b)
    return out


def _align_detail(t: EntityShape, c: EntityShape, tolerance: float) -> dict:
    """align_score(t, c) with the reason-it-failed surfaced when None."""
    res = align_score(t.points, c.points)
    if res is not None:
        chamfer, scale = res
        return {
            "scale": scale,
            "scale_in_range": SCALE_MIN <= scale <= SCALE_MAX,
            "chamfer": chamfer,
            "chamfer_in_tol": chamfer <= tolerance,
            "tolerance": tolerance,
        }
    # Re-derive to expose why None was returned.
    tp = _resample_arclength(t.points, RESAMPLE_N)
    cp = _resample_arclength(c.points, RESAMPLE_N)
    if tp.shape[0] < 2 or cp.shape[0] < 2:
        return {"degenerate": True, "scale": None, "chamfer": None}
    t_c = tp - tp.mean(axis=0)
    c_c = cp - cp.mean(axis=0)
    tn = float(np.linalg.norm(t_c, axis=1).mean())
    cn = float(np.linalg.norm(c_c, axis=1).mean())
    if tn < 1e-9 or cn < 1e-9:
        return {"degenerate_norm": True, "scale": None, "chamfer": None}
    scale = tn / cn
    return {
        "scale": scale,
        "scale_in_range": False,
        "scale_min": SCALE_MIN, "scale_max": SCALE_MAX,
        "chamfer": None,
    }


def _multi_seed_trace(
    template_shapes: list[EntityShape],
    drawing: dict[str, EntityShape],
    skip: set[str],
    tolerance: float,
    focus_handles: set[str],
) -> dict:
    """Trace the _match_multi seed/pose loop, restricted to candidate seeds
    whose handle is in `focus_handles` to keep output bounded."""
    handles = list(drawing.keys())
    if not handles:
        return {"seed": None, "candidate_counts": {}, "candidates_tried": []}
    centroids = np.stack([drawing[h].centroid for h in handles])
    tree = cKDTree(centroids)

    counts = {
        t.handle: sum(
            1 for h in handles
            if h not in skip and signatures_compatible(t, drawing[h])
        )
        for t in template_shapes
    }
    seed = min(template_shapes, key=lambda t: counts[t.handle])
    others = [t for t in template_shapes if t is not seed]

    seed_centered = seed.points - seed.centroid
    seed_axes, _ = _pca_axes(seed_centered)
    others_local: list[tuple[EntityShape, np.ndarray]] = []
    for t in others:
        local = (t.centroid - seed.centroid) @ seed_axes.T
        others_local.append((t, local))

    pos_tol = max(0.1, tolerance * 2)
    sign_variants = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

    cand_traces = []
    for cand_handle in handles:
        if cand_handle in skip:
            continue
        if cand_handle not in focus_handles:
            continue
        cand = drawing[cand_handle]
        seed_gate = _gate_detail(seed, cand)
        if not seed_gate["compatible"]:
            cand_traces.append({
                "candidate_seed": cand_handle,
                "seed_gate": seed_gate,
                "skipped": "seed_gate_failed",
            })
            continue
        cand_axes, _ = _pca_axes(cand.points - cand.centroid)
        variants_trace = []
        consistent_found = False
        for sx, sy in sign_variants:
            scaled_axes = cand_axes * np.array([[sx], [sy]])
            matched_handles = [cand_handle]
            others_trace = []
            consistent = True
            for t, local_pos in others_local:
                expected = local_pos @ scaled_axes + cand.centroid
                nearby_idx = tree.query_ball_point(expected, r=pos_tol)
                nearby = []
                best_handle: str | None = None
                best_score = float("inf")
                for ni in nearby_idx:
                    h = handles[ni]
                    if h in skip or h in matched_handles:
                        nearby.append({"handle": h, "skipped": True})
                        continue
                    sig_ok = signatures_compatible(t, drawing[h])
                    entry: dict = {"handle": h, "sig_ok": sig_ok}
                    if sig_ok:
                        res = align_score(t.points, drawing[h].points)
                        if res is not None:
                            entry["chamfer"] = res[0]
                            entry["scale"] = res[1]
                            if res[0] <= tolerance and res[0] < best_score:
                                best_handle = h
                                best_score = res[0]
                        else:
                            entry["align_rejected"] = True
                    nearby.append(entry)
                others_trace.append({
                    "template_handle": t.handle,
                    "expected_pos": [float(expected[0]), float(expected[1])],
                    "nearby": nearby,
                    "chosen": best_handle,
                    "chosen_score": (
                        best_score if best_handle is not None else None
                    ),
                })
                if best_handle is None:
                    consistent = False
                    break
                matched_handles.append(best_handle)
            variants_trace.append({
                "sign": [sx, sy],
                "consistent": consistent,
                "matched_handles": matched_handles,
                "others": others_trace,
            })
            if consistent:
                consistent_found = True
                break
        cand_traces.append({
            "candidate_seed": cand_handle,
            "seed_gate": seed_gate,
            "consistent_found": consistent_found,
            "variants": variants_trace,
        })

    return {
        "seed": seed.handle,
        "candidate_counts": counts,
        "candidates_tried": cand_traces,
        "pos_tol": pos_tol,
    }


def diagnose_swap(
    handles_a: list[str],
    handles_b: list[str],
    drawing_shapes: dict[str, EntityShape],
    tolerance: float | None = None,
    *,
    strategy: str = "chamfer",
    bbox_ratio: float | None = None,
) -> dict:
    """Run find_matches in both directions (A→drawing, B→drawing) and emit
    everything needed to pinpoint where the two directions diverge:

    - per-entity raw signatures for both patterns
    - per-pair (a_i, b_j) gate breakdown + alignment, in both directions
    - focused multi-seed pose trace restricted to the other pattern's handles
    - the two match outputs and a one-line asymmetry summary

    Used by /api/files/{file_id}/match-swap. The result is large but bounded:
    O(|A|·|B|) pairs + per-pattern multi trace over the opposing handle set.
    """
    if tolerance is None:
        tolerance = TOLERANCE_ABS
    a_shapes = [drawing_shapes[h] for h in handles_a if h in drawing_shapes]
    b_shapes = [drawing_shapes[h] for h in handles_b if h in drawing_shapes]

    pairs = []
    for a in a_shapes:
        for b in b_shapes:
            pairs.append({
                "a_handle": a.handle,
                "b_handle": b.handle,
                "forward": {
                    "gates": _gate_detail(a, b),
                    "align": _align_detail(a, b, tolerance),
                },
                "reverse": {
                    "gates": _gate_detail(b, a),
                    "align": _align_detail(b, a, tolerance),
                },
            })

    out_ab = find_matches(
        handles_a, drawing_shapes, tolerance=tolerance,
        strategy=strategy, bbox_ratio=bbox_ratio,
    )
    out_ba = find_matches(
        handles_b, drawing_shapes, tolerance=tolerance,
        strategy=strategy, bbox_ratio=bbox_ratio,
    )

    def _match_summary(out: MatchOutput) -> dict:
        return {
            "matches": [
                {"handles": m.handles, "score": m.score, "scale": m.scale}
                for m in out.matches
            ],
            "near_misses": [
                {"handles": n.handles, "score": n.score,
                 "scale": n.scale, "reason": n.reason}
                for n in out.near_misses
            ],
        }

    a_set, b_set = set(handles_a), set(handles_b)

    def _found_opposing(out: MatchOutput, target: set[str]) -> bool:
        for m in out.matches:
            if set(m.handles) & target:
                return True
        return False

    multi_trace: dict = {}
    if (
        strategy == "chamfer"
        and len(a_shapes) > 1
        and len(b_shapes) > 1
    ):
        multi_trace = {
            "a_template": _multi_seed_trace(
                a_shapes, drawing_shapes, a_set, tolerance, focus_handles=b_set,
            ),
            "b_template": _multi_seed_trace(
                b_shapes, drawing_shapes, b_set, tolerance, focus_handles=a_set,
            ),
        }

    return {
        "tolerance": tolerance,
        "strategy": strategy,
        "bbox_ratio": bbox_ratio,
        "tunables": {
            "PATH_LENGTH_RATIO": PATH_LENGTH_RATIO,
            "RADIUS_RATIO": RADIUS_RATIO,
            "SIGMA_RATIO_TOL": SIGMA_RATIO_TOL,
            "SCALE_MIN": SCALE_MIN,
            "SCALE_MAX": SCALE_MAX,
            "RESAMPLE_N": RESAMPLE_N,
        },
        "pattern_a": [_shape_summary(s) for s in a_shapes],
        "pattern_b": [_shape_summary(s) for s in b_shapes],
        "pairs": pairs,
        "ab_match": _match_summary(out_ab),
        "ba_match": _match_summary(out_ba),
        "asymmetric": {
            "a_template_finds_b": _found_opposing(out_ab, b_set),
            "b_template_finds_a": _found_opposing(out_ba, a_set),
        },
        "multi_trace": multi_trace,
    }
