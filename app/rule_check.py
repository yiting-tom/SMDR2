"""Design Rule Checking (DRC) — mock for now.

DRC is product-scoped: it sees every uploaded DXF in the product, keyed
by role (SBT, BD, POD, RING). The real implementation lives elsewhere;
this module provides a correctly-shaped mock so the UI and downstream
integration can be developed end-to-end.

Input
-----
`check_rules(product_id, dxfs_by_role)` where `dxfs_by_role` is:

    {
        "SBT": {"file_id": ..., "dxf_path": ..., "match_json": {...},
                "entity_shapes": {handle: EntityShape}},
        "BD":  {...},
        "POD": {...},
        "RING": {...},
    }

(roles absent from the product simply don't appear as keys.)

Output — RuleChecking JSON
--------------------------
    {
        "<ruleName>": {
            "checkRule":  str,
            "pass":        bool,
            "handleIds":  [str, ...],  // entities for the UI to highlight
        }, ...
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.matching import EntityShape


MatchJson = dict[str, list[list[str]]]
RoleBundle = dict[str, dict]   # {role: {match_json, entity_shapes, ...}}
RuleResult = dict[str, dict[str, object]]


# Thresholds (mm)
SUBSTRATE_TO_SMD_MIN_DIST = 5.0


# ---- helpers --------------------------------------------------------------
def _first_match_handles(match_json: MatchJson, class_prefix: str) -> list[str] | None:
    """First-occurrence entity handles for a class. None if no match exists."""
    for key, matches in match_json.items():
        if key.startswith(f"{class_prefix}.") and matches:
            return list(matches[0])
    return None


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

    # ---- Rule1 (single-DXF, BD): substrate-to-first-SMD distance > 5 mm
    bd = dxfs_by_role.get("BD")
    if bd is None:
        results["Rule1"] = {
            "checkRule": "BD DXF required for substrate-to-SMD distance check (not uploaded)",
            "pass": False,
            "handleIds": [],
        }
    else:
        substrate = _first_match_handles(bd["match_json"], "substrate")
        first_smd = _first_match_handles(bd["match_json"], "smd")
        shapes = bd["entity_shapes"]
        if not substrate or not first_smd:
            results["Rule1"] = {
                "checkRule": (
                    "BD must contain at least one substrate and one SMD; "
                    f"found substrate={'yes' if substrate else 'no'}, "
                    f"smd={'yes' if first_smd else 'no'}"
                ),
                "pass": False,
                "handleIds": (substrate or []) + (first_smd or []),
            }
        else:
            sub_c = _combined_centroid(shapes, substrate)
            smd_c = _combined_centroid(shapes, first_smd)
            if sub_c is None or smd_c is None:
                results["Rule1"] = {
                    "checkRule": "BD substrate/SMD centroid could not be computed",
                    "pass": False,
                    "handleIds": list(substrate) + list(first_smd),
                }
            else:
                dist = float(np.linalg.norm(sub_c - smd_c))
                results["Rule1"] = {
                    "checkRule": (
                        f"BD: substrate-to-first-SMD distance must exceed "
                        f"{SUBSTRATE_TO_SMD_MIN_DIST} mm (actual: {dist:.3f} mm)"
                    ),
                    "pass": dist > SUBSTRATE_TO_SMD_MIN_DIST,
                    "handleIds": list(substrate) + list(first_smd),
                }

    # ---- Rule2 (cross-DXF SBT × POD): bga_ball count consistency
    sbt = dxfs_by_role.get("SBT")
    pod = dxfs_by_role.get("POD")
    if sbt is None or pod is None:
        results["Rule2"] = {
            "checkRule": (
                "SBT and POD both required for BGA-count consistency check "
                f"(SBT={'present' if sbt else 'missing'}, "
                f"POD={'present' if pod else 'missing'})"
            ),
            "pass": False,
            "handleIds": [],
        }
    else:
        sbt_bga = _count_for_prefix(sbt["match_json"], "bga_ball")
        pod_bga = _count_for_prefix(pod["match_json"], "bga_ball")
        sbt_handles = []
        for key, matches in sbt["match_json"].items():
            if key.startswith("bga_ball."):
                for m in matches: sbt_handles.extend(m)
        pod_handles = []
        for key, matches in pod["match_json"].items():
            if key.startswith("bga_ball."):
                for m in matches: pod_handles.extend(m)
        results["Rule2"] = {
            "checkRule": (
                f"SBT BGA count must equal POD BGA count "
                f"(SBT={sbt_bga}, POD={pod_bga})"
            ),
            "pass": sbt_bga == pod_bga,
            # Sample a few from each side so the panel hover stays snappy.
            "handleIds": sbt_handles[:50] + pod_handles[:50],
        }

    return results
