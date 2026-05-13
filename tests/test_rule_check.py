"""Mock DRC: Rule1 = substrate-to-first-SMD distance > 5mm."""

from __future__ import annotations

from app.matching import EntityShape
from app.rule_check import check_rules, SUBSTRATE_TO_SMD_MIN_DIST


def _shape(handle, x, y):
    """Build an EntityShape for a single point at (x, y) — enough geometry to
    have a centroid at that location, which is all Rule1 needs."""
    return EntityShape.from_points(handle, [(x, y), (x + 1.0, y), (x + 1.0, y + 1.0), (x, y + 1.0), (x, y)])


def test_output_shape_per_rule():
    result = check_rules("ignored.dxf", {})
    assert isinstance(result, dict)
    assert "Rule1" in result
    payload = result["Rule1"]
    assert isinstance(payload["checkRule"], str)
    assert isinstance(payload["pass"], bool)
    assert isinstance(payload["handleIds"], list)


def test_rule1_passes_when_far_apart():
    match_json = {
        "substrate.0": [["S1"]],
        "smd.0":       [["A", "B", "C"]],
    }
    shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 100, 0),       # 100mm away — much > 5
        "B":  _shape("B", 102, 0),
        "C":  _shape("C", 104, 0),
    }
    result = check_rules("x.dxf", match_json, entity_shapes=shapes)
    assert result["Rule1"]["pass"] is True
    assert "S1" in result["Rule1"]["handleIds"]
    assert set(result["Rule1"]["handleIds"]) >= {"S1", "A", "B", "C"}


def test_rule1_fails_when_too_close():
    match_json = {
        "substrate.0": [["S1"]],
        "smd.0":       [["A", "B", "C"]],
    }
    shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 1, 0),         # ~1mm away — under threshold
        "B":  _shape("B", 1.5, 0),
        "C":  _shape("C", 2, 0),
    }
    result = check_rules("x.dxf", match_json, entity_shapes=shapes)
    assert result["Rule1"]["pass"] is False
    assert f"{SUBSTRATE_TO_SMD_MIN_DIST}" in result["Rule1"]["checkRule"]


def test_rule1_fails_without_substrate():
    match_json = {"smd.0": [["A", "B", "C"]]}
    shapes = {"A": _shape("A", 0, 0), "B": _shape("B", 1, 0), "C": _shape("C", 2, 0)}
    result = check_rules("x.dxf", match_json, entity_shapes=shapes)
    assert result["Rule1"]["pass"] is False


def test_rule1_fails_without_smd():
    match_json = {"substrate.0": [["S1"]]}
    shapes = {"S1": _shape("S1", 0, 0)}
    result = check_rules("x.dxf", match_json, entity_shapes=shapes)
    assert result["Rule1"]["pass"] is False
