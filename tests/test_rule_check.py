"""Tests for `app.rule_check` — the adapter to the external rule-checking
team's in-tree module.

These tests exercise the boundary contract (adapter forwards the bundle
path, envelope validation rejects malformed output, output flows through
verbatim on the happy path). Rule logic itself lives in
`app.external_rule_check` and is owned by the external team — their
test suite covers rule-specific behaviour.
"""

from __future__ import annotations

import json

import pytest

from app.rule_check import (
    RuleCheckOutputError,
    _validate_envelope,
    check_rules,
)


# ---- envelope helper -----------------------------------------------------

_VALID_PARTS = {"SBT", "BD", "POD", "RING", "LID"}


def _check_envelope(result):
    """Validate the RuleChecking JSON shape — mirror of the invariants
    enforced by `app.rule_check._validate_envelope`. Used by tests that
    construct expected results to make sure their fixtures stay valid."""
    assert isinstance(result, dict)
    for rule_name, payload in result.items():
        assert isinstance(rule_name, str)
        assert isinstance(payload["pass"], bool)
        assert isinstance(payload["text"], str)
        assert isinstance(payload["rules"], list)
        for sub in payload["rules"]:
            assert sub["part"] in _VALID_PARTS
            assert isinstance(sub["text"], str) and sub["text"]
            for key in ("from", "tol"):
                assert sub.get(key) is None or isinstance(sub[key], str)
            # `to` is str | list[str] | None; non-empty list elements
            # must be non-empty strings.
            to_val = sub.get("to")
            if to_val is not None:
                if isinstance(to_val, list):
                    assert to_val, "empty list `to` is illegal"
                    for el in to_val:
                        assert isinstance(el, str) and el
                else:
                    assert isinstance(to_val, str)
            tt = sub.get("tol_text")
            assert tt is None or isinstance(tt, str)
            has_handle = (
                sub.get("from") is not None
                or to_val is not None
                or sub.get("tol") is not None
            )
            if has_handle:
                assert sub["file_id"] is not None
            assert sub.get("from") is not None or sub.get("tol") is not None
            if to_val is not None:
                assert sub.get("from") is not None
            if sub.get("tol_text") is not None:
                assert sub.get("tol") is not None


def _ok_result():
    """A minimal-but-valid RuleChecking result the validator accepts.
    Built once so every test that needs a happy-path payload can lean
    on the same shape."""
    return {
        "Rule1": {
            "pass": True,
            "text": "substrate-to-SMD distance check",
            "rules": [
                {
                    "part": "BD",
                    "file_id": "abc123",
                    "from": "S1",
                    "to":   "A1",
                    "text": "distance = 8.5 mm (> 5)",
                    "tol":      None,
                    "tol_text": None,
                },
            ],
        },
    }


# ---- adapter forwards bundle path verbatim ------------------------------

def test_adapter_forwards_bundle_path(monkeypatch):
    """`check_rules(product_id, bundle_dir)` SHALL pass both arguments
    through to the external function and return its result unmodified."""
    captured: dict = {}
    expected = _ok_result()

    def fake(product_id, bundle_dir):
        captured["product_id"] = product_id
        captured["bundle_dir"] = bundle_dir
        return expected

    monkeypatch.setattr("app.rule_check._external_check_rules", fake)

    out = check_rules("p", "/tmp/fake-bundle")
    assert captured == {"product_id": "p", "bundle_dir": "/tmp/fake-bundle"}
    assert out is expected  # verbatim, no copy / no mutation


def test_adapter_passes_path_object_as_str(monkeypatch):
    """If the caller hands in a `pathlib.Path`, the adapter SHALL still
    pass a `str` to the external function (their signature is `str`)."""
    from pathlib import Path

    captured: dict = {}

    def fake(product_id, bundle_dir):
        captured["bundle_dir"] = bundle_dir
        return _ok_result()

    monkeypatch.setattr("app.rule_check._external_check_rules", fake)

    check_rules("p", Path("/tmp/x"))
    assert captured["bundle_dir"] == "/tmp/x"
    assert isinstance(captured["bundle_dir"], str)


def test_adapter_accepts_empty_rules(monkeypatch):
    """A rule with `rules: []` is valid — the envelope only requires
    `pass` / `text` on the outer payload."""
    result = {"Rule0": {"pass": True, "text": "all good", "rules": []}}
    monkeypatch.setattr("app.rule_check._external_check_rules", lambda *_: result)
    assert check_rules("p", "/tmp") is result


def test_adapter_accepts_tol_only_sub_rule(monkeypatch):
    """A sub-rule MAY set `tol` alone (no `from`, no `to`). The viewer
    will highlight `tol` and render `tol_text` next to it."""
    result = {
        "Rule1": {
            "pass": False,
            "text": "annotation-only finding",
            "rules": [{
                "part": "BD",
                "file_id": "abc",
                "from": None,
                "to":   None,
                "text": "see annotation",
                "tol":      "Z1",
                "tol_text": "out of tolerance",
            }],
        },
    }
    monkeypatch.setattr("app.rule_check._external_check_rules", lambda *_: result)
    assert check_rules("p", "/tmp") is result
    _check_envelope(result)


# ---- envelope validation rejects each invariant violation ---------------

def _run_with_fake(monkeypatch, payload):
    monkeypatch.setattr("app.rule_check._external_check_rules", lambda *_: payload)
    return check_rules("p", "/tmp")


def test_adapter_rejects_non_dict_result(monkeypatch):
    with pytest.raises(RuleCheckOutputError):
        _run_with_fake(monkeypatch, ["not", "a", "dict"])


def test_adapter_rejects_handle_without_file_id(monkeypatch):
    """`from`/`to`/`tol` set ⇒ `file_id` MUST be set."""
    bad = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": None,
                "from": "AB12", "to": None,
                "text": "missing file_id",
                "tol": None, "tol_text": None,
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="file_id"):
        _run_with_fake(monkeypatch, bad)


def test_text_only_sub_rule_is_accepted(monkeypatch):
    """A sub-rule MAY have all of `from` / `to` / `tol` / `tol_text` null.
    Such "text-only" sub-rules carry only `part` and `text` and surface
    in the viewer sidebar as informational entries (no canvas highlight).

    Regression: previously the adapter raised "must set at least one of
    `from`, `tol`" on this shape, forcing rule authors to invent
    placeholder handles. See `rule-json-accept-text-only-sub-rules`.
    """
    good = {
        "R": {
            "pass": True, "text": "x",
            "rules": [{
                "part": "BD", "file_id": None,
                "from": None, "to": None,
                "text": "All SMDs pass the substrate clearance check.",
                "tol": None, "tol_text": None,
            }],
        },
    }
    # MUST NOT raise — the all-null handle case is now valid.
    result = _run_with_fake(monkeypatch, good)
    assert result is good


def test_validator_still_rejects_other_invariants(monkeypatch):
    """Lock the adjacent invariants alongside the relaxed `from`/`tol`
    constraint. Relaxing "must set at least one of `from`, `tol`" MUST
    NOT accidentally widen any of these other invariants — text-only
    is the only new accepted shape.
    """
    # (1) `to` set but `from` null — still invalid.
    bad_to_without_from = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": "abc",
                "from": None, "to": "AB",
                "text": "to without from",
                "tol": None, "tol_text": None,
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="`to`"):
        _run_with_fake(monkeypatch, bad_to_without_from)

    # (2) `tol_text` set but `tol` null — still invalid.
    bad_tol_text_without_tol = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": None,
                "from": None, "to": None,
                "text": "tol_text without tol",
                "tol": None, "tol_text": "stranded label",
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="tol_text"):
        _run_with_fake(monkeypatch, bad_tol_text_without_tol)

    # (3) Handle set but `file_id` null — still invalid.
    bad_handle_without_file_id = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": None,
                "from": "AB12", "to": None,
                "text": "from without file_id",
                "tol": None, "tol_text": None,
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="file_id"):
        _run_with_fake(monkeypatch, bad_handle_without_file_id)


def test_adapter_rejects_to_without_from(monkeypatch):
    """`to` MAY only be set when `from` is also set."""
    bad = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": "abc",
                "from": None, "to": "AB",
                "text": "to without from",
                "tol": None, "tol_text": None,
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="`to`"):
        _run_with_fake(monkeypatch, bad)


def test_adapter_rejects_empty_text_when_sub_rules_present(monkeypatch):
    """A present sub-rule MUST carry non-empty `text`."""
    bad = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": "abc",
                "from": "X1", "to": None,
                "text": "",
                "tol": None, "tol_text": None,
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="text"):
        _run_with_fake(monkeypatch, bad)


def test_adapter_rejects_invalid_part(monkeypatch):
    bad = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "TODO", "file_id": "abc",
                "from": "X1", "to": None,
                "text": "wrong part",
                "tol": None, "tol_text": None,
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="part"):
        _run_with_fake(monkeypatch, bad)


def test_adapter_rejects_tol_text_without_tol(monkeypatch):
    bad = {
        "R": {
            "pass": False, "text": "x",
            "rules": [{
                "part": "BD", "file_id": "abc",
                "from": "X1", "to": None,
                "text": "spurious tol_text",
                "tol": None, "tol_text": "orphan",
            }],
        },
    }
    with pytest.raises(RuleCheckOutputError, match="tol_text"):
        _run_with_fake(monkeypatch, bad)


def test_adapter_rejects_missing_outer_keys(monkeypatch):
    """Each rule payload MUST carry `pass`, `text`, `rules`."""
    bad = {"R": {"pass": True, "rules": []}}  # missing `text`
    with pytest.raises(RuleCheckOutputError, match="text"):
        _run_with_fake(monkeypatch, bad)


def test_adapter_rejects_non_bool_pass(monkeypatch):
    bad = {"R": {"pass": "yes", "text": "x", "rules": []}}
    with pytest.raises(RuleCheckOutputError, match="bool"):
        _run_with_fake(monkeypatch, bad)


# ---- stub raises until the external team commits their module -----------

def test_stub_raises_not_implemented(monkeypatch):
    """The default `app.external_rule_check.check_rules` raises until
    the external rule-checking team commits their real module — that's
    the loud-failure mode design.md specified."""
    monkeypatch.delenv("SMDR2_DEV_MOCK_DRC", raising=False)
    from app.external_rule_check import check_rules as stub
    with pytest.raises(NotImplementedError, match="external rule module"):
        stub("p", "/tmp")


# ---- dev-mode mock (SMDR2_DEV_MOCK_DRC=1) -------------------------------

def _write_dev_bundle(root, files: dict[str, dict]):
    """Write a minimal bundle the dev mock can read.

    ``files`` maps file_id → ``{"role": str, "match_json": dict}``. We
    only write ``manifest.json`` + ``match/<file_id>.json`` since the
    mock never reads the DXFs."""
    (root / "match").mkdir(exist_ok=True)
    manifest = {"files": [
        {"role": spec["role"], "file_id": fid, "match_json": f"match/{fid}.json"}
        for fid, spec in files.items()
    ]}
    (root / "manifest.json").write_text(json.dumps(manifest))
    for fid, spec in files.items():
        (root / "match" / f"{fid}.json").write_text(json.dumps(spec["match_json"]))


def test_dev_mock_emits_three_display_modes(monkeypatch, tmp_path):
    """`SMDR2_DEV_MOCK_DRC=1` → adapter dispatches to the dev mock,
    which emits MockDistance (from+to) + MockHighlight (from only) +
    MockTolerance (tol+tol_text) so the viewer can smoke-test all
    three display modes off one fixture."""
    monkeypatch.setenv("SMDR2_DEV_MOCK_DRC", "1")
    _write_dev_bundle(tmp_path, {
        "abc12345": {
            "role": "BD",
            "match_json": {
                "substrate.0": [["S1"]],
                "smd_2t.0":    [["A1", "A2"]],
                "pin_1.0":     [["P1"]],
            },
        },
    })

    result = check_rules("p", tmp_path)
    _check_envelope(result)
    assert set(result.keys()) == {"MockDistance", "MockHighlight", "MockTolerance"}

    dist = result["MockDistance"]["rules"][0]
    assert dist["from"] is not None and dist["to"] is not None

    hl = result["MockHighlight"]["rules"][0]
    assert hl["from"] is not None and hl["to"] is None and hl["tol"] is None

    tol = result["MockTolerance"]["rules"][0]
    assert tol["from"] is None and tol["to"] is None
    assert tol["tol"] is not None and tol["tol_text"] is not None


def test_dev_mock_emits_one_sub_rule_per_role(monkeypatch, tmp_path):
    """A typical product (SBT + BD + POD) produces one sub-rule per
    role on each of the three mock rules — the viewer can click into
    each role's DXF independently and see its own highlight."""
    monkeypatch.setenv("SMDR2_DEV_MOCK_DRC", "1")
    _write_dev_bundle(tmp_path, {
        "sbt00001": {
            "role": "SBT",
            "match_json": {
                "bga_ball.0": [["B1"], ["B2"], ["B3"]],
                "substrate.0": [["S_sbt"]],
            },
        },
        "bd000001": {
            "role": "BD",
            "match_json": {
                "substrate.0": [["S_bd"]],
                "smd_2t.0":    [["A1", "A2"]],
                "pin_1.0":     [["P1"]],
            },
        },
        "pod00001": {
            "role": "POD",
            "match_json": {
                "bga_ball.0":  [["B_pod_1"], ["B_pod_2"]],
                "substrate.0": [["S_pod"]],
            },
        },
    })

    result = check_rules("p", tmp_path)
    _check_envelope(result)

    # Each rule has exactly one sub-rule per role (3 here).
    for rule_name in ("MockDistance", "MockHighlight", "MockTolerance"):
        parts = sorted(s["part"] for s in result[rule_name]["rules"])
        assert parts == ["BD", "POD", "SBT"], f"{rule_name}: got {parts!r}"

    # Roles map to the right file_id — clicking a SBT sub-rule must
    # navigate to the SBT DXF (not BD's).
    for rule_name in ("MockDistance", "MockHighlight", "MockTolerance"):
        for sub in result[rule_name]["rules"]:
            expected_fid = {"SBT": "sbt00001", "BD": "bd000001", "POD": "pod00001"}[sub["part"]]
            assert sub["file_id"] == expected_fid


def test_dev_mock_skips_roles_with_no_candidates(monkeypatch, tmp_path):
    """A role whose Match JSON has no non-empty match groups produces
    zero sub-rules for that role — the mock silently skips it rather
    than emitting an envelope-invalid handle-less sub-rule."""
    monkeypatch.setenv("SMDR2_DEV_MOCK_DRC", "1")
    _write_dev_bundle(tmp_path, {
        "sbt00001": {
            "role": "SBT",
            "match_json": {"bga_ball.0": [["B1"]]},
        },
        "bd000001": {
            "role": "BD",
            "match_json": {},   # no matches at all
        },
    })

    result = check_rules("p", tmp_path)
    _check_envelope(result)
    # Only SBT contributes — BD is silent.
    for rule_name in ("MockHighlight", "MockTolerance"):
        parts = [s["part"] for s in result[rule_name]["rules"]]
        assert parts == ["SBT"]


def test_dev_mock_empty_bundle_still_valid_envelope(monkeypatch, tmp_path):
    """No files in the bundle → the mock emits three rules with empty
    `rules` lists, which is still a valid envelope."""
    monkeypatch.setenv("SMDR2_DEV_MOCK_DRC", "1")
    (tmp_path / "manifest.json").write_text(json.dumps({"files": []}))

    result = check_rules("p", tmp_path)
    _check_envelope(result)
    for rule in result.values():
        assert rule["rules"] == []


def test_dev_mock_handles_view_prefixed_keys(monkeypatch, tmp_path):
    """Match-JSON keys may be `<class>.<idx>` or `<view>.<class>.<idx>`.
    The mock's class extractor MUST handle both — otherwise production
    bundles (which usually carry side-region prefixes) would silently
    produce zero candidates."""
    monkeypatch.setenv("SMDR2_DEV_MOCK_DRC", "1")
    _write_dev_bundle(tmp_path, {
        "abc12345": {
            "role": "BD",
            "match_json": {
                "top_view.substrate.0": [["S1"]],
                "top_view.smd_2t.0":    [["A1"]],
            },
        },
    })

    result = check_rules("p", tmp_path)
    _check_envelope(result)
    # MockDistance picks the two classes within the same view, so
    # rules is non-empty for the BD role.
    assert len(result["MockDistance"]["rules"]) == 1
    assert result["MockDistance"]["rules"][0]["part"] == "BD"


# ---- multi-`to` support (list of handles) ------------------------------
# Backward-compatible extension: scalar `to` keeps working, list-form
# `to` is the new fan shape. The adapter preserves the on-the-wire
# form verbatim (no scalar↔list normalisation).

def test_validate_accepts_scalar_to_string(monkeypatch):
    """Baseline: the existing scalar-`to` form still validates after the
    multi-`to` change is in."""
    result = _ok_result()
    monkeypatch.setattr("app.rule_check._external_check_rules",
                        lambda *_: result)
    out = check_rules("p", "/tmp/b")
    # Scalar form preserved verbatim — adapter does NOT auto-promote.
    assert out["Rule1"]["rules"][0]["to"] == "A1"


def test_validate_accepts_non_empty_list_to(monkeypatch):
    """List-form `to` validates and is returned verbatim."""
    result = {
        "FanRule": {
            "pass": False, "text": "min spacing violations",
            "rules": [
                {
                    "part": "BD", "file_id": "abc123",
                    "from": "S1",
                    "to": ["A1", "A2", "A3"],
                    "text": "S1 too close to A1/A2/A3",
                    "tol": None, "tol_text": None,
                },
            ],
        },
    }
    monkeypatch.setattr("app.rule_check._external_check_rules",
                        lambda *_: result)
    out = check_rules("p", "/tmp/b")
    # List form preserved verbatim — adapter does NOT auto-collapse to scalar.
    assert out["FanRule"]["rules"][0]["to"] == ["A1", "A2", "A3"]


def test_validate_rejects_empty_list_to(monkeypatch):
    """`to: []` is rejected — emitter must send `null` to mean no-to."""
    result = {
        "BadRule": {
            "pass": False, "text": "fail",
            "rules": [
                {
                    "part": "BD", "file_id": "abc123",
                    "from": "S1", "to": [],
                    "text": "msg",
                    "tol": None, "tol_text": None,
                },
            ],
        },
    }
    monkeypatch.setattr("app.rule_check._external_check_rules",
                        lambda *_: result)
    with pytest.raises(RuleCheckOutputError, match="empty"):
        check_rules("p", "/tmp/b")


def test_validate_rejects_list_to_with_non_string_element(monkeypatch):
    """`to: ["A1", 42]` is rejected; the message names the bad index."""
    result = {
        "BadRule": {
            "pass": False, "text": "fail",
            "rules": [
                {
                    "part": "BD", "file_id": "abc123",
                    "from": "S1", "to": ["A1", 42],
                    "text": "msg",
                    "tol": None, "tol_text": None,
                },
            ],
        },
    }
    monkeypatch.setattr("app.rule_check._external_check_rules",
                        lambda *_: result)
    with pytest.raises(RuleCheckOutputError, match=r"element #1"):
        check_rules("p", "/tmp/b")


def test_validate_rejects_list_to_with_empty_string(monkeypatch):
    """`to: ["A1", ""]` is rejected — every element must be non-empty."""
    result = {
        "BadRule": {
            "pass": False, "text": "fail",
            "rules": [
                {
                    "part": "BD", "file_id": "abc123",
                    "from": "S1", "to": ["A1", ""],
                    "text": "msg",
                    "tol": None, "tol_text": None,
                },
            ],
        },
    }
    monkeypatch.setattr("app.rule_check._external_check_rules",
                        lambda *_: result)
    with pytest.raises(RuleCheckOutputError, match="empty string"):
        check_rules("p", "/tmp/b")


def test_validate_rejects_list_to_without_from(monkeypatch):
    """`to: [...]` requires `from` non-null, mirrors the scalar rule."""
    result = {
        "BadRule": {
            "pass": False, "text": "fail",
            "rules": [
                {
                    "part": "BD", "file_id": "abc123",
                    "from": None, "to": ["A1", "A2"],
                    "text": "msg",
                    "tol": "T1", "tol_text": None,
                },
            ],
        },
    }
    monkeypatch.setattr("app.rule_check._external_check_rules",
                        lambda *_: result)
    with pytest.raises(RuleCheckOutputError, match="`to`"):
        check_rules("p", "/tmp/b")


# ---- coordinate-mode sub-rules (add-rule-check-coordinate-display) --------

def _env(sub):
    return {"R": {"pass": True, "text": "t", "rules": [sub]}}


def _coord_sub(**over):
    base = {"part": "BD", "text": "x"}
    base.update(over)
    return base


def test_point_to_point_coordinates_valid_without_file_id():
    # Coordinate group is self-located in the open frame — no file_id needed.
    _validate_envelope(_env(_coord_sub(
        from_coordinates=[10, 20], to_coordinates=[13, 24])))


def test_to_entity_polygon_valid_without_file_id():
    _validate_envelope(_env(_coord_sub(
        to_entity=[[0, 0], [5, 0], [5, 5], [0, 5]])))


def test_from_entity_alias_accepted():
    _validate_envelope(_env(_coord_sub(
        from_entity="AA00", to="AB12", file_id="f1")))


def test_coordinate_distance_and_outline_combine():
    _validate_envelope(_env(_coord_sub(
        from_coordinates=[0, 0], to_coordinates=[1, 1],
        to_entity=[[2, 2], [3, 3]])))


def test_unpaired_coordinates_rejected():
    with pytest.raises(RuleCheckOutputError, match="together|pair"):
        _validate_envelope(_env(_coord_sub(from_coordinates=[10, 20])))


def test_malformed_coordinate_rejected():
    with pytest.raises(RuleCheckOutputError, match="number"):
        _validate_envelope(_env(_coord_sub(
            from_coordinates=[10], to_coordinates=[1, 2])))


def test_nan_coordinate_rejected():
    with pytest.raises(RuleCheckOutputError, match="number"):
        _validate_envelope(_env(_coord_sub(
            from_coordinates=[0, 0], to_coordinates=[1, float("nan")])))


def test_bool_coordinate_rejected():
    with pytest.raises(RuleCheckOutputError, match="number"):
        _validate_envelope(_env(_coord_sub(
            from_coordinates=[0, True], to_coordinates=[1, 2])))


def test_empty_to_entity_rejected():
    with pytest.raises(RuleCheckOutputError, match="empty"):
        _validate_envelope(_env(_coord_sub(to_entity=[])))


def test_to_entity_bad_point_rejected():
    with pytest.raises(RuleCheckOutputError, match="to_entity"):
        _validate_envelope(_env(_coord_sub(to_entity=[[0, 0], [5]])))


def test_from_entity_conflicting_with_from_rejected():
    sub = {"part": "BD", "text": "x", "from": "AA00",
           "from_entity": "BB11", "to": "C", "file_id": "f1"}
    with pytest.raises(RuleCheckOutputError, match="disagree|from_entity"):
        _validate_envelope(_env(sub))
