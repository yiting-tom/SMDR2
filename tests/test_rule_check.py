"""Mock DRC: cross-DXF rules with from→to sub-rules."""

from __future__ import annotations

from app.matching import EntityShape
from app.rule_check import (
    SMD_TO_SUBSTRATE_MAX_DIST,
    SUBSTRATE_TO_SMD_MIN_DIST,
    _split_handle_prefix,
    check_rules,
)


def _shape(handle, x, y):
    return EntityShape.from_points(handle, [
        (x, y), (x + 1.0, y), (x + 1.0, y + 1.0), (x, y + 1.0), (x, y)
    ])


def _bundle(match_json, shapes, file_ids=None, dxf_paths=None):
    """Build a single-file role bundle. Defaults preserve the pre-multi-DXF
    shape so existing rule tests keep passing untouched; the optional
    `file_ids` / `dxf_paths` knobs let new tests assert against the
    expanded fields documented in the `design-rule-checking` spec."""
    file_ids = file_ids if file_ids is not None else ["unit_test"]
    dxf_paths = dxf_paths if dxf_paths is not None else ["unit_test.dxf"]
    return {
        "file_id": file_ids[0],
        "dxf_path": dxf_paths[0],
        "file_ids": list(file_ids),
        "dxf_paths": list(dxf_paths),
        "match_json": match_json,
        "entity_shapes": shapes,
    }


def _multi_bundle(per_file):
    """Build a multi-file role bundle that mirrors `run_product_rule_check`'s
    merge: every handle in `match_json` and every key in `entity_shapes`
    gets the `{file_id[:8]}:` prefix. `per_file` is a list of
    `(match_json, shapes)` tuples — one per source DXF. Synthetic
    file_ids are valid lowercase hex (`aaaa0001`, `aaaa0002`, …) so the
    prefix matches `_split_handle_prefix`'s strict-hex contract; real
    file_ids are SHA-256-derived and always hex."""
    merged_mj: dict[str, list[list[str]]] = {}
    merged_shapes: dict = {}
    file_ids: list[str] = []
    dxf_paths: list[str] = []
    for i, (mj, shapes) in enumerate(per_file, start=1):
        fid = f"aaaa{i:04x}"  # 8 lowercase-hex chars, matches the prod scheme
        file_ids.append(fid)
        dxf_paths.append(f"{fid}.dxf")
        prefix = f"{fid[:8]}:"
        for h, shape in shapes.items():
            merged_shapes[prefix + h] = shape
        for key, groups in mj.items():
            ns_groups = [[prefix + h for h in g] for g in groups]
            merged_mj.setdefault(key, []).extend(ns_groups)
    return {
        "file_id": file_ids[0],
        "dxf_path": dxf_paths[0],
        "file_ids": file_ids,
        "dxf_paths": dxf_paths,
        "match_json": merged_mj,
        "entity_shapes": merged_shapes,
    }


def _check_envelope(result):
    """Every rule must have the new top-level shape."""
    for name, payload in result.items():
        assert isinstance(name, str)
        assert isinstance(payload["pass"], bool)
        assert isinstance(payload["text"], str)
        assert isinstance(payload["rules"], list)
        for sub in payload["rules"]:
            assert sub["part"] in {"SBT", "BD", "POD", "RING", "LID"}
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
    mj = {"substrate.0": [["S1"]], "smd_2t.0": [["A", "B", "C"]]}
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
    mj = {"substrate.0": [["S1"]], "smd_2t.0": [["A", "B", "C"]]}
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
            "smd_2t.0":       [["A"], ["B"]],
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
            "smd_2t.0":       [["A"], ["B"]],
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


# ---- Handle-prefix split + multi-DXF bundle merge ----------------------
# See the `design-rule-checking` capability spec, requirement
# "Per-role bundle merging and handle prefix", for the contract these
# tests pin down.

def test_split_handle_prefix_round_trips_prefixed_handle():
    assert _split_handle_prefix("a3f12b9c:7AF") == ("a3f12b9c", "7AF")


def test_split_handle_prefix_returns_none_for_unprefixed_handle():
    assert _split_handle_prefix("7AF") == (None, "7AF")


def test_split_handle_prefix_requires_colon_separator():
    # 8 hex chars without the colon are NOT a prefix — the separator is
    # the contract invariant, not the hex shape.
    assert _split_handle_prefix("a3f12b9c") == (None, "a3f12b9c")


def test_multi_bundle_merges_two_files_with_distinct_prefixes():
    """Two synthetic BD files contribute one substrate handle each;
    the merged bundle must carry both handles, each under its own
    `{file_id[:8]}:` prefix, in both match_json and entity_shapes."""
    bundle = _multi_bundle([
        ({"substrate.0": [["A"]]}, {"A": _shape("A", 0, 0)}),
        ({"substrate.0": [["B"]]}, {"B": _shape("B", 100, 0)}),
    ])
    # File-list fields populated, singular fields default to the first.
    assert bundle["file_ids"] == ["aaaa0001", "aaaa0002"]
    assert bundle["file_id"] == "aaaa0001"
    assert bundle["dxf_paths"] == ["aaaa0001.dxf", "aaaa0002.dxf"]
    # Every handle in match_json is prefixed and resolvable in shapes.
    flat_handles = [h for groups in bundle["match_json"]["substrate.0"] for h in groups]
    assert flat_handles == ["aaaa0001:A", "aaaa0002:B"]
    for h in flat_handles:
        assert h in bundle["entity_shapes"]
        prefix, raw = _split_handle_prefix(h)
        assert prefix in {"aaaa0001", "aaaa0002"}
        assert raw in {"A", "B"}
    # check_rules treats the merged bundle as a normal role bundle —
    # the prefix is opaque to every existing helper.
    r = check_rules("p", {"BD": bundle})
    _check_envelope(r)


# ---- View-prefixed match keys (top_view / bottom_view / side_view) -----
# `app/main.py:save_match_json` rewrites every match instance's key from
# `<class>.<index>` to `<view>.<class>.<index>` when the file carries
# side-region rects. The rule_check helpers MUST recognise both shapes;
# otherwise every production bundle (which always has side regions in
# multi-DXF setups) returns 0 matches for every class.

def test_rule1_handles_view_prefixed_keys():
    """A top_view.substrate / top_view.smd_2t pair must be paired up
    just like the unprefixed flavour."""
    mj = {
        "top_view.substrate.0": [["S1"]],
        "top_view.smd_2t.0":    [["A", "B", "C"]],
    }
    shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 100, 0),
        "B":  _shape("B", 102, 0),
        "C":  _shape("C", 104, 0),
    }
    r = check_rules("p", {"BD": _bundle(mj, shapes)})
    assert r["Rule1"]["pass"] is True
    assert len(r["Rule1"]["rules"]) == 1
    sub = r["Rule1"]["rules"][0]
    assert sub["from"] == ["S1"]
    assert set(sub["to"]) == {"A", "B", "C"}
    # Origin label appears in the sub-rule text so the viewer / user can
    # see which coordinate space was checked.
    assert "top_view" in sub["text"]


def test_rule1_skips_cross_view_pairs():
    """Substrate in top_view, SMD-2T in bottom_view → no shared origin
    → no distance can be computed → rule fails with an explanatory text."""
    mj = {
        "top_view.substrate.0":    [["S1"]],
        "bottom_view.smd_2t.0":    [["A"]],
    }
    shapes = {
        "S1": _shape("S1", 0, 0),
        "A":  _shape("A", 1, 0),       # Numerically close, but irrelevant
    }
    r = check_rules("p", {"BD": _bundle(mj, shapes)})
    # Different coordinate spaces — must not pass, must not emit a
    # spurious "5.000 mm" sub-rule mixing the two.
    assert r["Rule1"]["pass"] is False
    assert r["Rule1"]["rules"] == []
    assert "same view" in r["Rule1"]["text"]


def test_rule1_emits_one_subrule_per_origin():
    """Substrate + SMD-2T in both top_view AND bottom_view → 2 sub-rules,
    one per view; rule passes only if every origin passes."""
    mj = {
        "top_view.substrate.0":    [["ST"]],
        "top_view.smd_2t.0":       [["AT"]],
        "bottom_view.substrate.0": [["SB"]],
        "bottom_view.smd_2t.0":    [["AB"]],
    }
    shapes = {
        "ST": _shape("ST", 0, 0),
        "AT": _shape("AT", 100, 0),   # top_view: far apart (pass)
        "SB": _shape("SB", 0, 0),
        "AB": _shape("AB", 1, 0),     # bottom_view: too close (fail)
    }
    r = check_rules("p", {"BD": _bundle(mj, shapes)})
    assert r["Rule1"]["pass"] is False  # one origin fails → rule fails
    assert len(r["Rule1"]["rules"]) == 2
    views = sorted(
        "top_view" if "top_view" in s["text"] else "bottom_view"
        for s in r["Rule1"]["rules"]
    )
    assert views == ["bottom_view", "top_view"]


def test_rule3_handles_view_prefixed_keys():
    """SMD-2T in top_view + substrate in top_view → distance computed in
    that view only."""
    bd = _bundle(
        match_json={
            "top_view.substrate.0": [["S"]],
            "top_view.smd_2t.0":    [["A"], ["B"]],
        },
        shapes={
            "S": EntityShape.from_points("S", [
                (0, 0), (10, 0), (10, 10), (0, 10), (0, 0)
            ]),
            "A": EntityShape.from_points("A", [(12, 4), (13, 5), (12, 4)]),
            "B": EntityShape.from_points("B", [(12, 6), (13, 7), (12, 6)]),
        },
    )
    r = check_rules("p", {"BD": bd})
    assert r["Rule3"]["pass"] is True
    assert len(r["Rule3"]["rules"]) == 2
    for sub in r["Rule3"]["rules"]:
        assert "top_view" in sub["text"]
        assert sub["to"] == ["S"]


def test_rule3_fails_smd_with_no_substrate_in_same_view():
    """SMD-2T in bottom_view with substrate only in top_view fails for
    that SMD — different coordinate spaces, not a 'close substrate'."""
    bd = _bundle(
        match_json={
            "top_view.substrate.0":    [["S"]],
            "bottom_view.smd_2t.0":    [["A"]],
        },
        shapes={
            "S": EntityShape.from_points("S", [
                (0, 0), (10, 0), (10, 10), (0, 10), (0, 0)
            ]),
            # Numerically close to S, but lives in a different view
            "A": EntityShape.from_points("A", [(12, 4), (13, 5), (12, 4)]),
        },
    )
    r = check_rules("p", {"BD": bd})
    assert r["Rule3"]["pass"] is False
    assert len(r["Rule3"]["rules"]) == 1
    sub = r["Rule3"]["rules"][0]
    assert sub["to"] == []  # no substrate paired up
    assert "no Substrate" in sub["text"]
    assert "bottom_view" in sub["text"]


def test_rule2_counts_aggregate_across_views():
    """Rule2 is intentionally aggregate — SBT bottom + POD top is still
    a legitimate count comparison."""
    sbt = _bundle(
        {"bottom_view.bga_ball.0": [["a"], ["b"], ["c"]]}, {}
    )
    pod = _bundle(
        {"top_view.bga_ball.0": [["x"], ["y"], ["z"]]}, {}
    )
    r = check_rules("p", {"SBT": sbt, "POD": pod})
    assert r["Rule2"]["pass"] is True
    parts = sorted(s["part"] for s in r["Rule2"]["rules"])
    assert parts == ["POD", "SBT"]


# ---- Multi-DXF origin scoping ------------------------------------------
def test_rule1_scopes_distance_by_source_dxf():
    """BD role made of two DXFs, each contributing one substrate AND one
    SMD-2T. Distances must be computed within each DXF's coordinate
    space, never crossing the file_id prefix boundary."""
    bundle = _multi_bundle([
        (
            {"substrate.0": [["S"]], "smd_2t.0": [["A"]]},
            {"S": _shape("S", 0, 0),  "A": _shape("A", 100, 0)},   # far → pass
        ),
        (
            {"substrate.0": [["S"]], "smd_2t.0": [["A"]]},
            {"S": _shape("S", 0, 0),  "A": _shape("A",   1, 0)},   # close → fail
        ),
    ])
    r = check_rules("p", {"BD": bundle})
    # Two origins (one per DXF), one passes and one fails.
    assert len(r["Rule1"]["rules"]) == 2
    assert r["Rule1"]["pass"] is False
    # `file_id` field on each sub-rule is the contract bridge between
    # "internal merge prefix" and "external file identifier" — viewer /
    # dashboard route on `file_id`, NOT on a parsed handle prefix.
    file_ids = {s["file_id"] for s in r["Rule1"]["rules"]}
    assert file_ids == {"aaaa0001", "aaaa0002"}
    # Handles in `from`/`to` are RAW (no `<prefix>:` decoration) so the
    # viewer's primitive index (keyed by raw DXF handle) can resolve
    # them with strict equality.
    for sub in r["Rule1"]["rules"]:
        for h in (*sub["from"], *sub["to"]):
            assert ":" not in h, f"handle {h!r} still carries the merge prefix"


def test_rule1_skips_cross_dxf_pairs():
    """Substrate-only in DXF A, SMD-only in DXF B → no shared origin →
    Rule1 fails with the 'no comparable pair' message."""
    bundle = _multi_bundle([
        ({"substrate.0": [["S"]]}, {"S": _shape("S", 0, 0)}),
        ({"smd_2t.0":    [["A"]]}, {"A": _shape("A", 1, 0)}),
    ])
    r = check_rules("p", {"BD": bundle})
    assert r["Rule1"]["pass"] is False
    assert r["Rule1"]["rules"] == []
    assert "same view" in r["Rule1"]["text"]


# ---- sub-rule `file_id` field ------------------------------------------
# The viewer / dashboard route by `sub.file_id`, so the rule emit MUST
# carry it whenever the rule knows which DXF the sub-rule applies to.

def test_rule1_subrule_file_id_set_for_single_file_bundle():
    """Single-file bundle: file_prefix is None in the origin tuple, so
    `_resolve_file_id` falls back to `bundle["file_ids"][0]`."""
    mj = {"substrate.0": [["S1"]], "smd_2t.0": [["A"]]}
    shapes = {"S1": _shape("S1", 0, 0), "A": _shape("A", 100, 0)}
    bundle = _bundle(mj, shapes, file_ids=["only_file"], dxf_paths=["only_file.dxf"])
    r = check_rules("p", {"BD": bundle})
    assert len(r["Rule1"]["rules"]) == 1
    assert r["Rule1"]["rules"][0]["file_id"] == "only_file"
    # Handles stay raw because the input bundle never prefixed them.
    assert ":" not in r["Rule1"]["rules"][0]["from"][0]


def test_rule3_subrule_file_id_propagates_through_multi_file_bundle():
    """A multi-file BD where every DXF has both substrate + SMD-2T must
    emit one sub-rule per file, each tagged with the right file_id and
    carrying raw handles. Without this the viewer can't focus the
    correct DXF."""
    bundle = _multi_bundle([
        (
            {"substrate.0": [["S"]], "smd_2t.0": [["A"]]},
            {"S": _shape("S", 0, 0), "A": _shape("A", 12, 4)},  # close → pass (<5)
        ),
        (
            {"substrate.0": [["S"]], "smd_2t.0": [["A"]]},
            {"S": _shape("S", 0, 0), "A": _shape("A", 50, 0)},  # far → fail (>=5)
        ),
    ])
    r = check_rules("p", {"BD": bundle})
    assert len(r["Rule3"]["rules"]) == 2
    file_ids = {s["file_id"] for s in r["Rule3"]["rules"]}
    assert file_ids == {"aaaa0001", "aaaa0002"}
    for sub in r["Rule3"]["rules"]:
        for h in (*sub["from"], *sub["to"]):
            assert ":" not in h


def test_rule2_subrules_carry_file_id_of_picked_source_dxf():
    """Rule2 is aggregate-count, but the sub-rule's `file_id` still
    points at ONE concrete DXF per part so the viewer has a place to
    land. We pick the first file's match group via `_iter_class_groups`
    ordering."""
    sbt = _multi_bundle([
        ({"bga_ball.0": [["a"], ["b"]]}, {}),
        ({"bga_ball.0": [["c"]]},        {}),
    ])
    pod = _bundle(
        {"bga_ball.0": [["x"], ["y"], ["z"]]}, {},
        file_ids=["pod_only"], dxf_paths=["pod_only.dxf"],
    )
    r = check_rules("p", {"SBT": sbt, "POD": pod})

    by_part = {s["part"]: s for s in r["Rule2"]["rules"]}
    assert by_part["SBT"]["file_id"] == "aaaa0001"   # first-file-of-role pick
    assert by_part["POD"]["file_id"] == "pod_only"
    # Handles must be raw + must belong to the picked file (the SBT
    # sub-rule must NOT reach across to `aaaa0002`).
    for h in (*by_part["SBT"]["from"], *by_part["SBT"]["to"]):
        assert h in {"a", "b"}, f"unexpected handle {h!r} — not from picked file"
    for h in (*by_part["POD"]["from"], *by_part["POD"]["to"]):
        assert h in {"x", "y", "z"}


def test_single_file_bundle_carries_unprefixed_handles():
    """`_bundle` (the default single-file helper) must not prefix
    anything — that contract guarantees existing rule tests keep
    passing untouched after the multi-DXF merge landed."""
    bd = _bundle(
        match_json={"substrate.0": [["S1"]], "smd_2t.0": [["A", "B", "C"]]},
        shapes={
            "S1": _shape("S1", 0, 0),
            "A":  _shape("A", 100, 0),
            "B":  _shape("B", 102, 0),
            "C":  _shape("C", 104, 0),
        },
    )
    assert bd["file_ids"] == ["unit_test"]
    for h in bd["entity_shapes"]:
        prefix, _ = _split_handle_prefix(h)
        assert prefix is None  # no merge prefix on a single-file bundle
