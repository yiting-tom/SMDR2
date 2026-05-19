"""Design Rule Checking (DRC) — mock for now.

DRC is product-scoped: it receives every uploaded DXF in the product
keyed by role (SBT, BD, POD, RING) and returns RuleChecking JSON whose
*sub-rules* each describe a from→to relationship on a specific DXF that
the viewer can highlight + draw an annotation line for.

RuleChecking JSON format:
    {
        "<ruleName>": {
            "pass": bool,
            "text": str,            // overall rule description
            "rules": [              // zero or more concrete checks
                {
                    "part": "SBT"|"BD"|"POD"|"RING",
                    "from": [handleID],        // single source entity
                    "to":   [handleID],        // single target entity
                    "text": str                // per-sub-rule message
                },
                ...
            ]
        },
        ...
    }

The viewer draws the *shortest distance* between the from and to
entities — i.e., the segment between the closest pair of vertices
across the two shapes. The from/to lists are arrays for forward-
compat (e.g., if a future rule needs to reference a group of
entities); the viewer collects vertices across every handle in each
list and finds the global minimum-distance pair.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.matching import EntityShape


MatchJson = dict[str, list[list[str]]]
RoleBundle = dict[str, dict]
SubRule = dict[str, object]
RuleResult = dict[str, dict[str, object]]


SUBSTRATE_TO_SMD_MIN_DIST = 5.0
SMD_TO_SUBSTRATE_MAX_DIST = 5.0


# ---- helpers --------------------------------------------------------------
_VIEW_PREFIXES = ("top_view", "bottom_view", "side_view")


def _parse_key(key: str) -> tuple[str | None, str] | None:
    """Split a match-JSON key into ``(view, class)``.

    Match keys produced by ``app/main.py:save_match_json`` come in two
    shapes:

    - ``<class>.<idx>`` — instance bbox-center falls outside every side
      region (or no side regions are defined on the file).
    - ``<view>.<class>.<idx>`` — instance was assigned to ``top_view`` /
      ``bottom_view`` / ``side_view`` by ``split_matches_by_side``.

    Returns ``(view, class)`` for either shape, or ``None`` for anything
    that doesn't parse (e.g. a malformed key from an older snapshot).
    """
    parts = key.rsplit(".", 2)
    if len(parts) == 2:
        return (None, parts[0])
    if len(parts) == 3 and parts[0] in _VIEW_PREFIXES:
        return (parts[0], parts[1])
    return None


def _first_match_handles(
    match_json: MatchJson,
    class_prefix: str,
    view: str | None = "__any__",
) -> list[str] | None:
    """First match group's handles for ``class_prefix``.

    ``view`` selects which side regions count toward "first":

    - ``"__any__"`` (default): any view (or unassigned) — back-compat.
    - ``None``: only unassigned instances (``<class>.<idx>`` keys).
    - ``"top_view"`` / ``"bottom_view"`` / ``"side_view"``: only that view.
    """
    for key, matches in match_json.items():
        parsed = _parse_key(key)
        if parsed is None:
            continue
        v, cls = parsed
        if cls != class_prefix:
            continue
        if view != "__any__" and v != view:
            continue
        if matches:
            return list(matches[0])
    return None


def _all_match_groups(
    match_json: MatchJson,
    class_prefix: str,
    view: str | None = "__any__",
) -> list[list[str]]:
    """Every match group of the given class — one inner list per occurrence.

    See :func:`_first_match_handles` for the ``view`` parameter semantics.
    """
    out: list[list[str]] = []
    for key, matches in match_json.items():
        parsed = _parse_key(key)
        if parsed is None:
            continue
        v, cls = parsed
        if cls != class_prefix:
            continue
        if view != "__any__" and v != view:
            continue
        for m in matches:
            out.append(list(m))
    return out


def _all_handles_for_prefix(match_json: MatchJson, class_prefix: str) -> list[str]:
    out: list[str] = []
    for groups in _all_match_groups(match_json, class_prefix):
        out.extend(groups)
    return out


def _count_for_prefix(match_json: MatchJson, class_prefix: str) -> int:
    return sum(1 for _ in _all_match_groups(match_json, class_prefix))


def _iter_class_groups(
    match_json: MatchJson,
    class_prefix: str,
) -> list[tuple[tuple[str | None, str | None], list[str]]]:
    """Every match group with its (view, file_prefix) origin attached.

    Returns a list of ``((view, file_prefix), handles)`` tuples — one per
    match group of ``class_prefix``. ``view`` comes from the key
    (``None`` for unassigned keys); ``file_prefix`` is derived from the
    first handle via :func:`_split_handle_prefix` so it reflects which
    source DXF the group originated from (``None`` for single-file
    bundles whose handles were never prefixed). This is the canonical
    way for rules to scope geometric comparisons to a single coordinate
    space — handles from different views or different DXFs are not
    comparable by distance.
    """
    out: list[tuple[tuple[str | None, str | None], list[str]]] = []
    for key, matches in match_json.items():
        parsed = _parse_key(key)
        if parsed is None:
            continue
        v, cls = parsed
        if cls != class_prefix:
            continue
        for group in matches:
            if not group:
                continue
            file_prefix, _ = _split_handle_prefix(group[0])
            out.append(((v, file_prefix), list(group)))
    return out


# `{file_id[:8]}:` prefix added by `run_product_rule_check` when a role
# holds ≥ 2 DXFs so handles from different files don't collide inside
# the merged bundle. See the `design-rule-checking` capability spec —
# "Per-role bundle merging and handle prefix" — for the full contract.
_HANDLE_PREFIX_RE = re.compile(r"^([0-9a-f]{8}):(.+)$")


def _split_handle_prefix(h: str) -> tuple[str | None, str]:
    """Return `(file_id_prefix, raw_handle)` for a prefixed handle, or
    `(None, h)` when the input carries no prefix.

    The prefix is recognised only when the input starts with exactly 8
    lowercase-hex chars followed by `:`. Strings that look hex-like but
    lack the colon (e.g. an unprefixed handle that happens to be 8 hex
    chars) return `(None, input)` — the colon separator is the contract
    invariant, not the hex shape.

    Rules that need to fan out per source DXF SHOULD go through this
    helper rather than parsing the prefix inline; that keeps the
    `app/rule_check.py` helpers' "handles are opaque strings" invariant
    intact for every rule that doesn't care."""
    m = _HANDLE_PREFIX_RE.match(h)
    if m is None:
        return (None, h)
    return (m.group(1), m.group(2))


# Geometric shortest-distance helpers (mirror canvas.js shortestSegmentBetween).
def _collect_segments(shapes: dict[str, "EntityShape"], handles: list[str]) -> list[tuple]:
    """List of (a, b) point pairs forming the edges of every handle's geometry.
    Treats any shape with ≥ 3 vertices as closed (good enough for the closed
    polylines / circles / rectangles SMDR2 deals with)."""
    segs: list[tuple] = []
    for h in handles:
        s = shapes.get(h)
        if s is None:
            continue
        pts = s.points
        n = pts.shape[0] if pts.ndim == 2 else 0
        if n == 0:
            continue
        if n == 1:
            p = (float(pts[0, 0]), float(pts[0, 1]))
            segs.append((p, p))
            continue
        for i in range(1, n):
            segs.append((
                (float(pts[i - 1, 0]), float(pts[i - 1, 1])),
                (float(pts[i, 0]),     float(pts[i, 1])),
            ))
        if n >= 3:
            a = (float(pts[-1, 0]), float(pts[-1, 1]))
            b = (float(pts[0, 0]),  float(pts[0, 1]))
            if a != b:
                segs.append((a, b))
    return segs


def _point_to_segment_dist(p, a, b) -> float:
    dx = b[0] - a[0]; dy = b[1] - a[1]
    len2 = dx * dx + dy * dy
    if len2 == 0.0:
        ex = p[0] - a[0]; ey = p[1] - a[1]
        return (ex * ex + ey * ey) ** 0.5
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2
    if t < 0.0: t = 0.0
    elif t > 1.0: t = 1.0
    fx = a[0] + t * dx; fy = a[1] + t * dy
    ex = p[0] - fx; ey = p[1] - fy
    return (ex * ex + ey * ey) ** 0.5


def _shortest_distance(
    shapes: dict[str, "EntityShape"],
    handles_a: list[str],
    handles_b: list[str],
) -> float | None:
    segs_a = _collect_segments(shapes, handles_a)
    segs_b = _collect_segments(shapes, handles_b)
    if not segs_a or not segs_b:
        return None
    best = float("inf")
    seen_a: set = set()
    for u, v in segs_a:
        for p in (u, v):
            if p in seen_a:
                continue
            seen_a.add(p)
            for q1, q2 in segs_b:
                d = _point_to_segment_dist(p, q1, q2)
                if d < best:
                    best = d
    seen_b: set = set()
    for u, v in segs_b:
        for p in (u, v):
            if p in seen_b:
                continue
            seen_b.add(p)
            for q1, q2 in segs_a:
                d = _point_to_segment_dist(p, q1, q2)
                if d < best:
                    best = d
    return best


def _resolve_file_id(bundle: dict, file_prefix: str | None) -> str | None:
    """Map an ``_iter_class_groups`` origin's ``file_prefix`` back to
    the full ``file_id`` from the role bundle.

    - ``file_prefix=None`` (single-file bundle whose handles were never
      prefixed): returns the bundle's only file_id, i.e.
      ``bundle["file_ids"][0]``.
    - Prefixed: scans ``bundle["file_ids"]`` for the entry whose first
      8 hex chars equal the prefix. Returns ``None`` if no match (which
      means the bundle was malformed — caller should treat it as a bug
      rather than a soft failure).

    The full file_id is what the viewer / dashboard route on, so this
    lookup is the contract bridge between "internal merge prefix" and
    "external file identifier".
    """
    ids = bundle.get("file_ids") or []
    if file_prefix is None:
        return ids[0] if ids else None
    for fid in ids:
        if fid.startswith(file_prefix):
            return fid
    return None


def _strip_handle_prefixes(handles: list[str]) -> list[str]:
    """Drop the ``<file_id[:8]>:`` prefix from every handle, leaving
    raw DXF handles. Use this in the **emit** path of sub-rules so
    the viewer (whose primitive index is keyed by raw handles) can
    resolve them directly. Within a single sub-rule every handle is
    expected to share an origin, so this is safe to apply blindly."""
    return [_split_handle_prefix(h)[1] for h in handles]


def _origin_label(origin: tuple[str | None, str | None]) -> str:
    """Render a ``(view, file_prefix)`` origin for sub-rule text.

    Both components are optional: ``view=None`` means the instance was
    not assigned to any side region (a per-file ``unassigned`` bucket);
    ``file_prefix=None`` means the bundle is single-file and handles
    were never prefixed. The label only mentions whichever components
    are present, so single-file / no-side-region bundles still get a
    clean message.
    """
    view, file_prefix = origin
    bits: list[str] = []
    if view is not None:
        bits.append(view)
    if file_prefix is not None:
        bits.append(f"file={file_prefix}")
    return f"[{' | '.join(bits)}] " if bits else ""


# ---- main entry -----------------------------------------------------------
def check_rules(product_id: str, dxfs_by_role: RoleBundle) -> RuleResult:
    """Mock product-scoped DRC. Replace with the real implementation when ready."""
    results: RuleResult = {}

    # ---- Rule1: Substrate-to-first-SMD-2T shortest distance in BD -------
    # Geometric proximity is only meaningful when the two shapes share a
    # coordinate space, so we group by `(view, file_prefix)` origin and
    # emit one sub-rule per origin that has BOTH a substrate and an
    # SMD-2T. A BD bundle that splits views across multiple DXFs (or
    # carries multiple views inside a single DXF via side-region rects)
    # therefore produces ≥ 1 sub-rule — one geometrically valid check
    # per coordinate space.
    bd = dxfs_by_role.get("BD")
    rule1_sub: list[SubRule] = []
    rule1_pass = False
    rule1_text = "BD: Substrate-to-first-SMD-2T distance check"
    if bd is None:
        rule1_text = "BD DXF required (not uploaded)"
    else:
        shapes = bd["entity_shapes"]
        sub_groups = _iter_class_groups(bd["match_json"], "substrate")
        smd_groups = _iter_class_groups(bd["match_json"], "smd_2t")
        # First substrate / first SMD-2T per origin (key insertion order
        # preserves match-JSON ordering, which preserves template-index
        # ordering within each view).
        sub_first: dict[tuple[str | None, str | None], list[str]] = {}
        smd_first: dict[tuple[str | None, str | None], list[str]] = {}
        for origin, handles in sub_groups:
            sub_first.setdefault(origin, handles)
        for origin, handles in smd_groups:
            smd_first.setdefault(origin, handles)
        shared = [o for o in sub_first if o in smd_first]
        if not sub_first and not smd_first:
            rule1_text = "BD has no Substrate and no SMD-2T matches"
        elif not sub_first:
            rule1_text = "BD must contain at least one Substrate"
        elif not smd_first:
            rule1_text = "BD must contain at least one SMD-2T"
        elif not shared:
            rule1_text = (
                "BD has Substrate and SMD-2T but never in the same view/DXF "
                "— distance is not defined across coordinate spaces"
            )
        else:
            all_pass = True
            for origin in shared:
                substrate = sub_first[origin]
                first_smd = smd_first[origin]
                _, file_prefix = origin
                file_id = _resolve_file_id(bd, file_prefix)
                raw_substrate = _strip_handle_prefixes(substrate)
                raw_first_smd = _strip_handle_prefixes(first_smd)
                dist = _shortest_distance(shapes, substrate, first_smd)
                if dist is None:
                    all_pass = False
                    rule1_sub.append({
                        "part": "BD",
                        "file_id": file_id,
                        "from": raw_substrate,
                        "to":   raw_first_smd,
                        "text": f"{_origin_label(origin)}geometry could not be computed",
                    })
                    continue
                passes = dist > SUBSTRATE_TO_SMD_MIN_DIST
                if not passes:
                    all_pass = False
                rule1_sub.append({
                    "part": "BD",
                    "file_id": file_id,
                    "from": raw_substrate,
                    "to":   raw_first_smd,
                    "text": f"{_origin_label(origin)}distance = {dist:.3f} mm "
                            f"({'> ' if passes else '<= '}{SUBSTRATE_TO_SMD_MIN_DIST} mm)",
                })
            rule1_pass = all_pass
            rule1_text = (
                f"Substrate-to-first-SMD-2T distance must exceed "
                f"{SUBSTRATE_TO_SMD_MIN_DIST} mm in every view/DXF "
                f"({len(shared)} checked)"
            )
    results["Rule1"] = {"pass": rule1_pass, "text": rule1_text, "rules": rule1_sub}

    # ---- Rule2: SBT and POD must agree on BGA-ball count -----------------
    sbt = dxfs_by_role.get("SBT")
    pod = dxfs_by_role.get("POD")
    rule2_sub: list[SubRule] = []
    rule2_pass = False
    if sbt is None or pod is None:
        rule2_text = (
            f"SBT and POD both required (SBT={'yes' if sbt else 'no'}, "
            f"POD={'yes' if pod else 'no'})"
        )
    else:
        sbt_count = _count_for_prefix(sbt["match_json"], "bga_ball")
        pod_count = _count_for_prefix(pod["match_json"], "bga_ball")
        rule2_pass = sbt_count == pod_count
        rule2_text = f"SBT BGA count ({sbt_count}) must equal POD BGA count ({pod_count})"

        # Pick ONE file per part for the sub-rule's `file_id` so the
        # viewer / dashboard can navigate to a concrete DXF. We use the
        # first match group in `_iter_class_groups` order; for multi-file
        # roles every sub-rule's `from` / `to` handles therefore share
        # a single file's coordinate space (otherwise the viewer would
        # try to highlight handles from a DXF it doesn't have open).
        for part, bundle in (("SBT", sbt), ("POD", pod)):
            groups = _iter_class_groups(bundle["match_json"], "bga_ball")
            if not groups:
                continue
            (_, file_prefix), first_group = groups[0]
            file_id = _resolve_file_id(bundle, file_prefix)
            # Use only the groups that share the chosen file's origin so
            # `from` and `to` stay in one coordinate space.
            same_file_handles: list[str] = []
            for (_, fp), handles in groups:
                if fp == file_prefix:
                    same_file_handles.extend(handles)
            raw = _strip_handle_prefixes(same_file_handles)
            count = sbt_count if part == "SBT" else pod_count
            rule2_sub.append({
                "part": part,
                "file_id": file_id,
                "from": raw[:1],
                "to":   raw[-1:] if len(raw) > 1 else [],
                "text": f"{part} BGABall count = {count}",
            })
    results["Rule2"] = {"pass": rule2_pass, "text": rule2_text, "rules": rule2_sub}

    # ---- Rule3: every SMD-2T must be within 5 mm of Substrate in BD -----
    # Same origin-scoping as Rule1: each SMD-2T is compared only to
    # substrate(s) sharing its `(view, file_prefix)` coordinate space.
    # An SMD-2T with no substrate in its origin is reported as a
    # failure so it can't slip through silently.
    rule3_sub: list[SubRule] = []
    rule3_pass = False
    if bd is None:
        rule3_text = "BD DXF required for SMD-2T-to-Substrate proximity check (not uploaded)"
    else:
        shapes = bd["entity_shapes"]
        smd_groups_by_origin = _iter_class_groups(bd["match_json"], "smd_2t")
        # Map every origin to its substrate handles (union of every
        # substrate match group in that origin). Multiple substrate
        # templates in the same coordinate space are concatenated so
        # _shortest_distance picks the closest substrate edge to the SMD.
        sub_by_origin: dict[tuple[str | None, str | None], list[str]] = {}
        for origin, handles in _iter_class_groups(bd["match_json"], "substrate"):
            sub_by_origin.setdefault(origin, []).extend(handles)
        if not smd_groups_by_origin:
            rule3_text = "BD has no SMD-2T matches to evaluate"
        elif not sub_by_origin:
            rule3_text = "BD must contain a Substrate"
        else:
            all_under = True
            # Stable per-origin index so the sub-rule text is readable
            # ("SMD-2T #1 in top_view", "#2 in top_view", "#1 in bottom_view").
            per_origin_idx: dict[tuple[str | None, str | None], int] = {}
            for origin, smd_handles in smd_groups_by_origin:
                idx = per_origin_idx.get(origin, 0) + 1
                per_origin_idx[origin] = idx
                _, file_prefix = origin
                file_id = _resolve_file_id(bd, file_prefix)
                raw_smd = _strip_handle_prefixes(smd_handles)
                substrate = sub_by_origin.get(origin)
                if not substrate:
                    all_under = False
                    rule3_sub.append({
                        "part": "BD",
                        "file_id": file_id,
                        "from": raw_smd,
                        "to":   [],
                        "text": f"{_origin_label(origin)}SMD-2T #{idx} has no "
                                f"Substrate in the same view/DXF",
                    })
                    continue
                dist = _shortest_distance(shapes, smd_handles, substrate)
                if dist is None:
                    continue
                passes = dist < SMD_TO_SUBSTRATE_MAX_DIST
                if not passes:
                    all_under = False
                raw_substrate = _strip_handle_prefixes(substrate)
                rule3_sub.append({
                    "part": "BD",
                    "file_id": file_id,
                    "from": raw_smd,
                    "to":   raw_substrate,
                    "text": f"{_origin_label(origin)}SMD-2T #{idx} → Substrate "
                            f"= {dist:.3f} mm "
                            f"({'< ' if passes else '>= '}{SMD_TO_SUBSTRATE_MAX_DIST} mm)",
                })
            rule3_pass = all_under
            n_smd = len(smd_groups_by_origin)
            rule3_text = (
                f"Every SMD-2T must be within {SMD_TO_SUBSTRATE_MAX_DIST} mm "
                f"of the Substrate in the same view/DXF "
                f"({n_smd} SMD-2T{'' if n_smd == 1 else 's'} checked)"
            )
    results["Rule3"] = {"pass": rule3_pass, "text": rule3_text, "rules": rule3_sub}

    return results
