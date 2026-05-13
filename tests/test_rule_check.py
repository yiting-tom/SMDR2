"""Mock DRC: cross-DXF rules with from→to sub-rules."""

from __future__ import annotations

from app.matching import EntityShape
from app.rule_check import (
    SMD_TO_SUBSTRATE_MAX_DIST,
    SUBSTRATE_TO_SMD_MIN_DIST,
    check_rules,
)


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
    assert sub["from"] == ["S1"]
    # Rule1's `to` is the entire first-SMD match group (1+ handles); the
    # viewer collects vertices across all of them when computing the line.
    assert set(sub["to"]) == {"A", "B", "C"}
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


# ---- Rule3: per-SMD-to-substrate proximity (mock) -----------------------
def test_rule3_passes_when_every_smd_is_close():
    """SMD at (1, 0)–(2, 1) and substrate at (0, 0)–(10, 10): shortest
    distance is 0 because the SMD lies inside the substrate's bbox.
    We want a SMD strictly outside but within the threshold instead."""
    bd = _bundle(
        match_json={
            "substrate.0": [["S"]],
            "smd.0":       [["A"], ["B"]],
        },
        shapes={
            # Substrate footprint (10×10 square at origin)
            "S": EntityShape.from_points("S", [
                (0, 0), (10, 0), (10, 10), (0, 10), (0, 0)
            ]),
            # Two SMDs each ~2 mm away from the substrate edge
            "A": EntityShape.from_points("A", [
                (12, 4), (13, 4), (13, 5), (12, 5), (12, 4)
            ]),
            "B": EntityShape.from_points("B", [
                (12, 6), (13, 6), (13, 7), (12, 7), (12, 6)
            ]),
        },
    )
    r = check_rules("p", {"BD": bd})
    _check_envelope(r)
    assert r["Rule3"]["pass"] is True
    assert len(r["Rule3"]["rules"]) == 2
    for sub in r["Rule3"]["rules"]:
        assert sub["part"] == "BD"
        assert sub["to"] == ["S"]
        assert "<" in sub["text"]
        assert f"{SMD_TO_SUBSTRATE_MAX_DIST}" in sub["text"]


def test_rule3_fails_when_any_smd_is_too_far():
    bd = _bundle(
        match_json={
            "substrate.0": [["S"]],
            "smd.0":       [["A"], ["B"]],
        },
        shapes={
            "S": EntityShape.from_points("S", [
                (0, 0), (10, 0), (10, 10), (0, 10), (0, 0)
            ]),
            # Close — passes
            "A": EntityShape.from_points("A", [(12, 4), (13, 5), (12, 4)]),
            # Far — fails (15 mm gap)
            "B": EntityShape.from_points("B", [(25, 6), (26, 7), (25, 6)]),
        },
    )
    r = check_rules("p", {"BD": bd})
    assert r["Rule3"]["pass"] is False
    # Two sub-rules, one passing and one failing.
    statuses = [(">=" in s["text"]) for s in r["Rule3"]["rules"]]
    assert sum(statuses) == 1
    assert sum(not x for x in statuses) == 1


def test_rule3_handles_no_bd():
    r = check_rules("p", {})
    assert r["Rule3"]["pass"] is False
    assert "BD" in r["Rule3"]["text"]
    assert r["Rule3"]["rules"] == []


def test_rule3_handles_no_smds():
    bd = _bundle(
        match_json={"substrate.0": [["S"]]},
        shapes={"S": EntityShape.from_points("S", [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])},
    )
    r = check_rules("p", {"BD": bd})
    assert r["Rule3"]["pass"] is False
    assert "no SMD" in r["Rule3"]["text"]
    assert r["Rule3"]["rules"] == []
