"""Mock DRC: cross-DXF rules with from→to sub-rules."""

from __future__ import annotations

from app.matching import EntityShape
from app.rule_check import check_rules, SUBSTRATE_TO_SMD_MIN_DIST


def _shape(handle, x, y):
    return EntityShape.from_points(handle, [
        (x, y), (x + 1.0, y), (x + 1.0, y + 1.0), (x, y + 1.0), (x, y)
    ])


def _bundle(match_json, shapes):
    return {"match_json": match_json, "entity_shapes": shapes}


def _check_envelope(result):
    """Every rule must have the new top-level shape."""
    for name, payload in result.items():
        assert isinstance(name, str)
        assert isinstance(payload["pass"], bool)
        assert isinstance(payload["text"], str)
        assert isinstance(payload["rules"], list)
        for sub in payload["rules"]:
            assert sub["part"] in {"SBT", "BD", "POD", "RING"}
            assert isinstance(sub["from"], list)
            assert isinstance(sub["to"], list)
            assert isinstance(sub["text"], str)


def test_envelope_with_empty_input():
    r = check_rules("p", {})
    _check_envelope(r)
    # No BD / SBT / POD => both rules fail with empty sub-rule lists.
    assert r["Rule1"]["pass"] is False
    assert r["Rule1"]["rules"] == []
    assert r["Rule2"]["pass"] is False


def test_rule1_passes_with_far_apart_substrate_and_smd():
    mj = {"substrate.0": [["S1"]], "smd.0": [["A", "B", "C"]]}
    shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 100, 0),
        "B":  _shape("B", 102, 0),
        "C":  _shape("C", 104, 0),
    }
    r = check_rules("p", {"BD": _bundle(mj, shapes)})
    _check_envelope(r)
    assert r["Rule1"]["pass"] is True
    assert len(r["Rule1"]["rules"]) == 1
    sub = r["Rule1"]["rules"][0]
    assert sub["part"] == "BD"
    assert sub["from"] == ["S1"]                  # single handle
    assert sub["to"]   == ["A"]                   # single handle (first SMD)
    assert "distance" in sub["text"]


def test_rule1_fails_when_too_close():
    mj = {"substrate.0": [["S1"]], "smd.0": [["A", "B", "C"]]}
    shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 1, 0),
        "B":  _shape("B", 1.5, 0),
        "C":  _shape("C", 2, 0),
    }
    r = check_rules("p", {"BD": _bundle(mj, shapes)})
    assert r["Rule1"]["pass"] is False
    assert f"{SUBSTRATE_TO_SMD_MIN_DIST}" in r["Rule1"]["text"]
    # Even on failure we emit the sub-rule so the viewer can show the offending line.
    assert len(r["Rule1"]["rules"]) == 1


def test_rule1_no_subrules_when_bd_missing():
    r = check_rules("p", {"POD": _bundle({}, {})})
    assert r["Rule1"]["pass"] is False
    assert r["Rule1"]["rules"] == []
    assert "BD" in r["Rule1"]["text"]


def test_rule2_cross_dxf_match():
    sbt = _bundle({"bga_ball.0": [["a"], ["b"], ["c"]]}, {})
    pod = _bundle({"bga_ball.0": [["x"], ["y"], ["z"]]}, {})
    r = check_rules("p", {"SBT": sbt, "POD": pod})
    _check_envelope(r)
    assert r["Rule2"]["pass"] is True
    # One sub-rule for each part that has BGA balls.
    parts = sorted(s["part"] for s in r["Rule2"]["rules"])
    assert parts == ["POD", "SBT"]


def test_rule2_cross_dxf_count_mismatch_still_emits_subrules():
    sbt = _bundle({"bga_ball.0": [["a"], ["b"], ["c"]]}, {})
    pod = _bundle({"bga_ball.0": [["x"], ["y"]]}, {})
    r = check_rules("p", {"SBT": sbt, "POD": pod})
    assert r["Rule2"]["pass"] is False
    assert "3" in r["Rule2"]["text"] and "2" in r["Rule2"]["text"]
    # Sub-rules still tag the parts so the viewer can highlight where the
    # mismatch lives in each DXF.
    parts = sorted(s["part"] for s in r["Rule2"]["rules"])
    assert parts == ["POD", "SBT"]
