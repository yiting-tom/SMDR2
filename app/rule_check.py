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
                    "from": [handleID, ...],   // source entities
                    "to":   [handleID, ...],   // target entities
                    "text": str                // per-sub-rule message
                },
                ...
            ]
        },
        ...
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.matching import EntityShape


MatchJson = dict[str, list[list[str]]]
RoleBundle = dict[str, dict]
SubRule = dict[str, object]
RuleResult = dict[str, dict[str, object]]


SUBSTRATE_TO_SMD_MIN_DIST = 5.0


# ---- helpers --------------------------------------------------------------
def _first_match_handles(match_json: MatchJson, class_prefix: str) -> list[str] | None:
    for key, matches in match_json.items():
        if key.startswith(f"{class_prefix}.") and matches:
            return list(matches[0])
    return None


def _all_handles_for_prefix(match_json: MatchJson, class_prefix: str) -> list[str]:
    out: list[str] = []
    for key, matches in match_json.items():
        if key.startswith(f"{class_prefix}."):
            for m in matches:
                out.extend(m)
    return out


def _count_for_prefix(match_json: MatchJson, class_prefix: str) -> int:
    n = 0
    for key, matches in match_json.items():
        if key.startswith(f"{class_prefix}."):
            n += len(matches)
    return n


def _combined_centroid(
    entity_shapes: dict[str, "EntityShape"],
    handles: list[str],
) -> np.ndarray | None:
    pts = [entity_shapes[h].centroid for h in handles if h in entity_shapes]
    if not pts:
        return None
    return np.mean(np.stack(pts), axis=0)


# ---- main entry -----------------------------------------------------------
def check_rules(product_id: str, dxfs_by_role: RoleBundle) -> RuleResult:
    """Mock product-scoped DRC. Replace with the real implementation when ready."""
    results: RuleResult = {}

    # ---- Rule1: substrate-to-first-SMD distance in BD --------------------
    bd = dxfs_by_role.get("BD")
    rule1_sub: list[SubRule] = []
    rule1_pass = False
    rule1_text = "BD: substrate-to-first-SMD distance check"
    if bd is None:
        rule1_text = "BD DXF required (not uploaded)"
    else:
        substrate = _first_match_handles(bd["match_json"], "substrate")
        first_smd = _first_match_handles(bd["match_json"], "smd")
        shapes = bd["entity_shapes"]
        if not substrate or not first_smd:
            rule1_text = (
                f"BD must contain at least one substrate and one SMD "
                f"(substrate={'yes' if substrate else 'no'}, smd={'yes' if first_smd else 'no'})"
            )
        else:
            sub_c = _combined_centroid(shapes, substrate)
            smd_c = _combined_centroid(shapes, first_smd)
            if sub_c is None or smd_c is None:
                rule1_text = "BD substrate/SMD centroid could not be computed"
            else:
                dist = float(np.linalg.norm(sub_c - smd_c))
                rule1_pass = dist > SUBSTRATE_TO_SMD_MIN_DIST
                rule1_text = (
                    f"Substrate-to-first-SMD distance must exceed "
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
                "text": f"SBT bga_ball count = {sbt_count}",
            })
        if pod_handles:
            rule2_sub.append({
                "part": "POD",
                "from": pod_handles[:1],
                "to":   pod_handles[-1:] if len(pod_handles) > 1 else [],
                "text": f"POD bga_ball count = {pod_count}",
            })
    results["Rule2"] = {"pass": rule2_pass, "text": rule2_text, "rules": rule2_sub}

    return results
