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
def _first_match_handles(match_json: MatchJson, class_prefix: str) -> list[str] | None:
    for key, matches in match_json.items():
        if key.startswith(f"{class_prefix}.") and matches:
            return list(matches[0])
    return None


def _all_match_groups(match_json: MatchJson, class_prefix: str) -> list[list[str]]:
    """Every match group of the given class — one inner list per occurrence."""
    out: list[list[str]] = []
    for key, matches in match_json.items():
        if key.startswith(f"{class_prefix}."):
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


# ---- main entry -----------------------------------------------------------
def check_rules(product_id: str, dxfs_by_role: RoleBundle) -> RuleResult:
    """Mock product-scoped DRC. Replace with the real implementation when ready."""
    results: RuleResult = {}

    # ---- Rule1: Substrate-to-first-SMD-2T shortest distance in BD -------
    bd = dxfs_by_role.get("BD")
    rule1_sub: list[SubRule] = []
    rule1_pass = False
    rule1_text = "BD: Substrate-to-first-SMD-2T distance check"
    if bd is None:
        rule1_text = "BD DXF required (not uploaded)"
    else:
        substrate = _first_match_handles(bd["match_json"], "substrate")
        first_smd = _first_match_handles(bd["match_json"], "smd_2t")
        shapes = bd["entity_shapes"]
        if not substrate or not first_smd:
            rule1_text = (
                f"BD must contain at least one Substrate and one SMD-2T "
                f"(Substrate={'yes' if substrate else 'no'}, SMD-2T={'yes' if first_smd else 'no'})"
            )
        else:
            dist = _shortest_distance(shapes, substrate, first_smd)
            if dist is None:
                rule1_text = "BD Substrate/SMD-2T geometry could not be computed"
            else:
                rule1_pass = dist > SUBSTRATE_TO_SMD_MIN_DIST
                rule1_text = (
                    f"Substrate-to-first-SMD-2T distance must exceed "
                    f"{SUBSTRATE_TO_SMD_MIN_DIST} mm"
                )
                rule1_sub.append({
                    "part": "BD",
                    "from": list(substrate),
                    "to":   list(first_smd),
                    "text": f"distance = {dist:.3f} mm "
                            f"({'> ' if rule1_pass else '<= '}{SUBSTRATE_TO_SMD_MIN_DIST} mm)",
                })
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
        sbt_handles = _all_handles_for_prefix(sbt["match_json"], "bga_ball")
        pod_handles = _all_handles_for_prefix(pod["match_json"], "bga_ball")
        rule2_pass = sbt_count == pod_count
        rule2_text = f"SBT BGA count ({sbt_count}) must equal POD BGA count ({pod_count})"

        # Pair the first SBT BGA with the first POD BGA so the viewer (per
        # part) has a concrete from→to to draw. We emit one sub-rule per
        # part so the viewer for that DXF can render the annotation.
        if sbt_handles:
            rule2_sub.append({
                "part": "SBT",
                "from": sbt_handles[:1],
                "to":   sbt_handles[-1:] if len(sbt_handles) > 1 else [],
                "text": f"SBT BGABall count = {sbt_count}",
            })
        if pod_handles:
            rule2_sub.append({
                "part": "POD",
                "from": pod_handles[:1],
                "to":   pod_handles[-1:] if len(pod_handles) > 1 else [],
                "text": f"POD BGABall count = {pod_count}",
            })
    results["Rule2"] = {"pass": rule2_pass, "text": rule2_text, "rules": rule2_sub}

    # ---- Rule3: every SMD-2T must be within 5 mm of Substrate in BD -----
    rule3_sub: list[SubRule] = []
    rule3_pass = False
    if bd is None:
        rule3_text = "BD DXF required for SMD-2T-to-Substrate proximity check (not uploaded)"
    else:
        substrate = _first_match_handles(bd["match_json"], "substrate")
        smd_groups = _all_match_groups(bd["match_json"], "smd_2t")
        shapes = bd["entity_shapes"]
        if not substrate:
            rule3_text = "BD must contain a Substrate"
        elif not smd_groups:
            rule3_text = "BD has no SMD-2T matches to evaluate"
        else:
            all_under = True
            for i, smd_handles in enumerate(smd_groups):
                dist = _shortest_distance(shapes, smd_handles, substrate)
                if dist is None:
                    continue
                passes = dist < SMD_TO_SUBSTRATE_MAX_DIST
                if not passes:
                    all_under = False
                rule3_sub.append({
                    "part": "BD",
                    "from": list(smd_handles),
                    "to":   list(substrate),
                    "text": f"SMD-2T #{i + 1} → Substrate = {dist:.3f} mm "
                            f"({'< ' if passes else '>= '}{SMD_TO_SUBSTRATE_MAX_DIST} mm)",
                })
            rule3_pass = all_under
            rule3_text = (
                f"Every SMD-2T must be within {SMD_TO_SUBSTRATE_MAX_DIST} mm of the Substrate "
                f"({len(smd_groups)} SMD-2T{'' if len(smd_groups) == 1 else 's'} checked)"
            )
    results["Rule3"] = {"pass": rule3_pass, "text": rule3_text, "rules": rule3_sub}

    return results
