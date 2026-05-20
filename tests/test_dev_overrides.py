"""Dev-mode parameter override store.

Covers the contract from `openspec/changes/expose-dev-parameter-overrides`:
- defaults snapshot is taken at import time
- apply() validates atomically and mutates module attributes in place
- snapshot_non_default()/apply_snapshot() round-trip cleanly for worker handoff
- reset() restores compiled defaults
- the override mechanism actually flows through matcher / preprocess defaults
  (i.e. the default-arg refactor on `find_matches` / `signatures_compatible`
  did not break runtime lookup)
"""

from __future__ import annotations

import pytest

from app import dev_overrides, dxf, matching


@pytest.fixture(autouse=True)
def _reset_after():
    yield
    dev_overrides.reset()


def test_get_state_lists_every_allow_listed_entry():
    state = dev_overrides.read_state()
    names = {e["name"] for e in state}
    # Spec lock — drop a name here and the spec needs updating too.
    expected = {
        "SCALE_MIN", "SCALE_MAX", "TOLERANCE_ABS", "VERTEX_COUNT_RATIO",
        "PATH_LENGTH_RATIO", "RADIUS_RATIO", "SIGMA_RATIO_TOL",
        "RESAMPLE_N", "BRUTE_FORCE_CUTOFF",
        "BASE_TOLERANCE", "CURVE_FLATTENING_DISTANCE",
        "CIRCLE_MIN_VERTS", "CIRCLE_RADIAL_TOL",
        "MAX_PRIMS_PER_THUMB", "MAX_VERTICES_PER_POLYLINE",
    }
    assert expected <= names


def test_defaults_match_compiled_module_attributes():
    for entry in dev_overrides.read_state():
        assert entry["current"] == entry["default"]


def test_apply_mutates_target_module_attribute():
    dev_overrides.apply({"TOLERANCE_ABS": 0.07, "CIRCLE_MIN_VERTS": 12})
    assert matching.TOLERANCE_ABS == 0.07
    assert dxf.CIRCLE_MIN_VERTS == 12


def test_apply_rejects_unknown_key_without_mutation():
    prior = matching.TOLERANCE_ABS
    with pytest.raises(dev_overrides.ValidationError) as info:
        dev_overrides.apply({"NOPE": 1, "TOLERANCE_ABS": 0.02})
    assert "NOPE" in info.value.errors
    assert matching.TOLERANCE_ABS == prior


def test_apply_rejects_out_of_range_atomically():
    prior_tol = matching.TOLERANCE_ABS
    prior_min = dxf.CIRCLE_MIN_VERTS
    with pytest.raises(dev_overrides.ValidationError) as info:
        dev_overrides.apply({"TOLERANCE_ABS": 0.04, "CIRCLE_MIN_VERTS": -3})
    assert "CIRCLE_MIN_VERTS" in info.value.errors
    # Atomic: even though TOLERANCE_ABS validates fine, nothing is mutated.
    assert matching.TOLERANCE_ABS == prior_tol
    assert dxf.CIRCLE_MIN_VERTS == prior_min


def test_apply_rejects_bool_coercion():
    with pytest.raises(dev_overrides.ValidationError) as info:
        dev_overrides.apply({"RESAMPLE_N": True})
    assert "RESAMPLE_N" in info.value.errors


def test_apply_empty_payload_is_validation_error():
    with pytest.raises(dev_overrides.ValidationError):
        dev_overrides.apply({})


def test_reset_returns_to_compiled_defaults():
    dev_overrides.apply({"TOLERANCE_ABS": 0.07})
    assert matching.TOLERANCE_ABS == 0.07
    dev_overrides.reset()
    assert matching.TOLERANCE_ABS == dev_overrides.DEFAULTS["TOLERANCE_ABS"]


def test_snapshot_non_default_returns_only_overrides():
    assert dev_overrides.snapshot_non_default() == {}
    dev_overrides.apply({"RESAMPLE_N": 128})
    snap = dev_overrides.snapshot_non_default()
    assert snap == {"RESAMPLE_N": 128}


def test_apply_snapshot_round_trips_for_worker_handoff():
    dev_overrides.apply({"TOLERANCE_ABS": 0.03, "BASE_TOLERANCE": 0.05})
    snap = dev_overrides.snapshot_non_default()
    # Pretend we crossed a process boundary by resetting then re-applying.
    dev_overrides.reset()
    assert matching.TOLERANCE_ABS == dev_overrides.DEFAULTS["TOLERANCE_ABS"]
    dev_overrides.apply_snapshot(snap)
    assert matching.TOLERANCE_ABS == 0.03
    assert dxf.BASE_TOLERANCE == 0.05


def test_apply_snapshot_silently_skips_unknown_keys():
    dev_overrides.apply_snapshot({"TOLERANCE_ABS": 0.04, "FUTURE_KNOB": 9})
    assert matching.TOLERANCE_ABS == 0.04  # known key applied


def test_signatures_compatible_picks_up_runtime_override():
    """The default-arg refactor on `signatures_compatible` is the load-
    bearing piece that lets the override store flow through the matcher.
    This test fails immediately if the function reverts to capturing
    `PATH_LENGTH_RATIO` at def time."""
    from app.matching import EntityShape, signatures_compatible

    # Two shapes with a 50% path-length mismatch — passes default 0.20
    # band ⇒ no; fails default ⇒ yes; we want to confirm the gate moves.
    a = EntityShape.from_points(
        "a", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)],
    )
    b = EntityShape.from_points(
        "b", [(0.0, 0.0), (1.5, 0.0), (1.5, 1.5), (0.0, 1.5), (0.0, 0.0)],
    )
    # At default 0.20, the 50% length mismatch is rejected.
    assert signatures_compatible(a, b) is False
    # Widen the band via the override store; same call now passes.
    dev_overrides.apply({"PATH_LENGTH_RATIO": 0.9, "RADIUS_RATIO": 0.9})
    assert signatures_compatible(a, b) is True


def test_render_layer_svg_picks_up_max_prims_override():
    """`render_layer_svg` previously captured `MAX_PRIMS_PER_THUMB` at
    def time. Refactor must keep behaviour identical at default; this
    test guards the override path."""
    from app.dxf import render_layer_svg
    prims = [
        {"type": "line", "start": [i, 0], "end": [i + 1, 0],
         "color": "#fff", "layer": "L", "lineweight": 0.0, "handle": f"h{i}"}
        for i in range(50)
    ]
    indices = list(range(len(prims)))
    bbox = (0.0, 0.0, 50.0, 1.0)
    # Default cap (600) — all 50 lines fit.
    full = render_layer_svg(prims, indices, bbox)
    # Override down to 10 (allow-list floor); rendered SVG should contain
    # fewer <line> tags.
    dev_overrides.apply({"MAX_PRIMS_PER_THUMB": 10})
    capped = render_layer_svg(prims, indices, bbox)
    assert full.count("<line ") == 50
    assert capped.count("<line ") == 10
