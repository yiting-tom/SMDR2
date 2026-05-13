"""Design Rule Checking (DRC) — mock for now.

The real DRC accepts a DXF path and Match JSON; this mock currently
implements one geometric test rule:

    Rule1: substrate-to-first-SMD distance must exceed 5 mm.

Output shape — RuleChecking JSON:
    {
        "<ruleName>": {
            "checkRule":  str,
            "pass":       bool,
            "handleIds":  [str, ...],
        }, ...
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.matching import EntityShape


MatchJson = dict[str, list[list[str]]]
RuleResult = dict[str, dict[str, object]]

# Threshold for Rule1 (mm).
SUBSTRATE_TO_SMD_MIN_DIST = 5.0


def _first_match_handles(match_json: MatchJson, class_name: str) -> list[str] | None:
    """First-occurrence entity handles for a class. None if no match exists."""
    for key, matches in match_json.items():
        if key.startswith(f"{class_name}.") and matches:
            return list(matches[0])
    return None


def _combined_centroid(
    entity_shapes: dict[str, "EntityShape"],
    handles: list[str],
) -> np.ndarray | None:
    """Mean of centroids over the given handles."""
    pts = [entity_shapes[h].centroid for h in handles if h in entity_shapes]
    if not pts:
        return None
    return np.mean(np.stack(pts), axis=0)


def check_rules(
    dxf_path: str | Path,                                   # noqa: ARG001
    match_json: MatchJson,
    entity_shapes: dict[str, "EntityShape"] | None = None,
) -> RuleResult:
    """Mock DRC. Replace with the real implementation when ready."""
    results: RuleResult = {}

    # ---- Rule1: substrate-to-first-SMD distance > 5 mm -------------------
    substrate = _first_match_handles(match_json, "substrate")
    first_smd = _first_match_handles(match_json, "smd")

    if not substrate or not first_smd:
        passes = False
        desc = (
            "Substrate and first SMD must both be present; "
            f"found substrate={'yes' if substrate else 'no'}, "
            f"smd={'yes' if first_smd else 'no'}"
        )
        handle_ids = (substrate or []) + (first_smd or [])
    elif entity_shapes is None:
        passes = False
        desc = "Rule1 needs entity geometry (entity_shapes not provided)"
        handle_ids = (substrate or []) + (first_smd or [])
    else:
        sub_c = _combined_centroid(entity_shapes, substrate)
        smd_c = _combined_centroid(entity_shapes, first_smd)
        if sub_c is None or smd_c is None:
            passes = False
            desc = "Substrate or SMD centroid could not be computed"
        else:
            dist = float(np.linalg.norm(sub_c - smd_c))
            passes = dist > SUBSTRATE_TO_SMD_MIN_DIST
            desc = (
                f"Substrate-to-first-SMD distance must exceed "
                f"{SUBSTRATE_TO_SMD_MIN_DIST} mm (actual: {dist:.3f} mm)"
            )
        handle_ids = list(substrate) + list(first_smd)

    results["Rule1"] = {
        "checkRule": desc,
        "pass": passes,
        "handleIds": handle_ids,
    }

    return results
