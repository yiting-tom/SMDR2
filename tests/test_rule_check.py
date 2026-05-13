"""Mock DRC: product-scoped (cross-DXF) rules."""

from __future__ import annotations

from app.matching import EntityShape
from app.rule_check import check_rules, SUBSTRATE_TO_SMD_MIN_DIST


def _shape(handle, x, y):
    """A small square at (x, y) — enough geometry to have a centroid."""
    return EntityShape.from_points(handle, [
        (x, y), (x + 1.0, y), (x + 1.0, y + 1.0), (x, y + 1.0), (x, y)
    ])


def _bd(match_json, shapes):
    return {"match_json": match_json, "entity_shapes": shapes}


def test_output_shape():
    result = check_rules("p", {})
    assert isinstance(result, dict)
    for rule_name, payload in result.items():
        assert isinstance(rule_name, str)
        assert "checkRule" in payload
        assert "pass" in payload
        assert "handleIds" in payload
        assert isinstance(payload["pass"], bool)
        assert isinstance(payload["handleIds"], list)


def test_rule1_passes_when_bd_substrate_and_smd_are_far_apart():
    bd_mj = {
        "substrate.0": [["S1"]],
        "smd.0":       [["A", "B", "C"]],
    }
    bd_shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 100, 0),
        "B":  _shape("B", 102, 0),
        "C":  _shape("C", 104, 0),
    }
    res = check_rules("p", {"BD": _bd(bd_mj, bd_shapes)})
    assert res["Rule1"]["pass"] is True
    assert "S1" in res["Rule1"]["handleIds"]


def test_rule1_fails_when_too_close():
    bd_mj = {"substrate.0": [["S1"]], "smd.0": [["A", "B", "C"]]}
    bd_shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 1, 0),
        "B":  _shape("B", 1.5, 0),
        "C":  _shape("C", 2, 0),
    }
    res = check_rules("p", {"BD": _bd(bd_mj, bd_shapes)})
    assert res["Rule1"]["pass"] is False
    assert f"{SUBSTRATE_TO_SMD_MIN_DIST}" in res["Rule1"]["checkRule"]


def test_rule1_fails_without_bd():
    res = check_rules("p", {"POD": _bd({}, {})})
    assert res["Rule1"]["pass"] is False
    assert "BD" in res["Rule1"]["checkRule"]


def test_rule2_cross_dxf_bga_count_match():
    sbt = _bd({"bga_ball.0": [["a"], ["b"], ["c"]]}, {})
    pod = _bd({"bga_ball.0": [["x"], ["y"], ["z"]]}, {})
    res = check_rules("p", {"SBT": sbt, "POD": pod})
    assert res["Rule2"]["pass"] is True


def test_rule2_cross_dxf_bga_count_mismatch():
    sbt = _bd({"bga_ball.0": [["a"], ["b"], ["c"]]}, {})
    pod = _bd({"bga_ball.0": [["x"], ["y"]]}, {})
    res = check_rules("p", {"SBT": sbt, "POD": pod})
    assert res["Rule2"]["pass"] is False
    assert "3" in res["Rule2"]["checkRule"] and "2" in res["Rule2"]["checkRule"]


def test_rule2_fails_when_either_role_missing():
    res = check_rules("p", {"SBT": _bd({}, {})})
    assert res["Rule2"]["pass"] is False
    assert "POD" in res["Rule2"]["checkRule"]
