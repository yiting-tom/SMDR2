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
SCALE_MIN = 0.95
SCALE_MAX = 1.05
TOLERANCE_ABS = 0.05            # ε for chamfer (world units / mm)
VERTEX_COUNT_RATIO = 0.25       # candidate must be within ±25% vertex count
PATH_LENGTH_RATIO = 0.20        # ±20% path length (covers scale range + sampling noise)
RADIUS_RATIO = 0.20             # ±20% max-radius-from-centroid (rotation-invariant)
SIGMA_RATIO_TOL = 0.15          # absolute Δ on σ₂/σ₁ ∈ [0,1] — principal-axis aspect
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
CIRCLE_RADIUS_KEY_DIGITS = 6
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
        if arr.shape[0] >= 2 and np.allclose(arr[0], arr[-1]):
            arr = arr[:-1]
        centroid = arr.mean(axis=0)
        d = arr - centroid
        radius = float(np.linalg.norm(d, axis=1).max())
        s1, s2 = _pca_singular_values(d)
        return cls(handle, arr, centroid, radius, path_length, arr.shape[0],
                   kind, s1, s2)


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
    """One EntityShape per DXF handle, aggregating all its primitives' points."""
    from app.library import collect_entity_kinds, collect_entity_points
    shapes: dict[str, EntityShape] = {}
    for h in handle_index:
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
    # bias scale or Chamfer. _resample_arclength absorbs the closing-vertex
    # dedup that the previous serial path did explicitly.
    template_pts = _resample_arclength(template_pts, RESAMPLE_N)
    candidate_pts = _resample_arclength(candidate_pts, RESAMPLE_N)
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
    path_length_ratio: float = PATH_LENGTH_RATIO,
    radius_ratio: float = RADIUS_RATIO,
) -> bool:
    """Cheap dimensional + aspect agreement test. Returns True when `a` and
    `b` are within size and aspect tolerance.

    The two dimensional gates accept per-call overrides so signature-mode
    classes can tighten them (e.g., 0.05) without changing the global
    defaults that chamfer-mode classes rely on as a pre-filter. The
    σ-ratio aspect gate is intentionally not overridable here — its
    discrimination is qualitative (rect vs square, thin vs blob), not
    size-tolerant.
    """
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
    tolerance: float = TOLERANCE_ABS,
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
    return _match_multi(template_shapes, drawing_shapes, template_handle_set, tolerance)


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
    tolerance: float = TOLERANCE_ABS,
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
    return _match_multi(template_shapes, drawing_shapes, skip, tolerance)


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
    hits = _get_radius_buckets(drawing).get(key, [])
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

    # Template-side once
    t_resampled = _resample_arclength(template.points, RESAMPLE_N)
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

        c_resampled = _resample_arclength(shape.points, RESAMPLE_N)
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
    tolerance: float,
) -> MatchOutput:
    """Pose-based multi-entity matching.

    The previous "gather everything in radius + bulk chamfer" approach failed
    when neighbouring patterns were close together: extra entities got swept
    in, blowing the point-count gate.

    New approach: pick the rarest template entity as a seed; encode every
    other template entity's centroid in the seed's PCA-local frame; for each
    candidate seed in the drawing, hypothesise the same pose and look up
    each other template entity at its *predicted* position (not anywhere in
    a radius). Then verify each per-entity shape match independently.
    """
    handles = list(drawing.keys())
    centroids = np.stack([drawing[h].centroid for h in handles])
    tree = cKDTree(centroids)

    # Rarest template entity = best seed (smallest signature-compatible set).
    def candidate_count(t: EntityShape) -> int:
        return sum(1 for h in handles if h not in skip and signatures_compatible(t, drawing[h]))

    seed = min(template_shapes, key=candidate_count)
    others = [t for t in template_shapes if t is not seed]

    # Encode other template entities' positions in seed's PCA-local frame.
    seed_centered = seed.points - seed.centroid
    seed_axes, _ = _pca_axes(seed_centered)
    others_local: list[tuple[EntityShape, np.ndarray]] = []
    for t in others:
        local = (t.centroid - seed.centroid) @ seed_axes.T
        others_local.append((t, local))

    # Position tolerance for "is this entity at the predicted spot": small
    # absolute buffer above chamfer tolerance to absorb PCA + scale noise.
    pos_tol = max(0.1, tolerance * 2)

    sign_variants = [(1, 1), (1, -1), (-1, 1), (-1, -1)]

    matches: list[MatchResult] = []
    near: list[NearMiss] = []
    seen_groups: set[tuple[str, ...]] = set()

    for cand_handle in handles:
        if cand_handle in skip:
            continue
        cand = drawing[cand_handle]
        if not signatures_compatible(seed, cand):
            continue

        # Candidate's PCA frame in world.
        cand_axes, _ = _pca_axes(cand.points - cand.centroid)

        for sx, sy in sign_variants:
            # 4 mirror/flip variants of how candidate's PCA aligns to template's.
            scaled_axes = cand_axes * np.array([[sx], [sy]])

            matched_handles = [cand_handle]
            scores: list[float] = []
            consistent = True

            for t, local_pos in others_local:
                expected = local_pos @ scaled_axes + cand.centroid
                nearby_idx = tree.query_ball_point(expected, r=pos_tol)
                best_handle: str | None = None
                best_score = float("inf")
                for ni in nearby_idx:
                    h = handles[ni]
                    if h in skip or h in matched_handles:
                        continue
                    if not signatures_compatible(t, drawing[h]):
                        continue
                    res = align_score(t.points, drawing[h].points)
                    if res is None:
                        continue
                    score, _ = res
                    if score <= tolerance and score < best_score:
                        best_handle = h
                        best_score = score
                if best_handle is None:
                    consistent = False
                    break
                matched_handles.append(best_handle)
                scores.append(best_score)

            if consistent:
                group = tuple(sorted(matched_handles))
                if group not in seen_groups:
                    seen_groups.add(group)
                    matches.append(MatchResult(
                        handles=list(group),
                        score=max(scores) if scores else 0.0,
                        scale=1.0,
                    ))
                break  # don't try other sign variants — first hit wins

    return MatchOutput(matches=matches, near_misses=near)
