"""Unit tests for `app.side_regions.suppress_contained_matches`.

Pure dict-in / dict-out tests for the contained-match suppression rule:
within each class (pooled across view prefixes), drop a match instance whose
consumed-handle set is a proper subset of another instance's; the superset
wins. See openspec/changes/suppress-contained-matches/specs for the scenarios.
"""

from __future__ import annotations

import app.side_regions as sr
from app.side_regions import parse_match_key, suppress_contained_matches


def _union_by_class(d: dict[str, list[list[str]]]) -> dict[str, set[str]]:
    """Per-class handle union — the shape scan-all / prematch collapse to."""
    acc: dict[str, set[str]] = {}
    for key, instances in d.items():
        parsed = parse_match_key(key)
        snake = parsed[1] if parsed else key
        bucket = acc.setdefault(snake, set())
        for hl in instances:
            bucket.update(hl)
    return acc


# ---- Core rule ------------------------------------------------------------

def test_proper_subset_dropped_superset_kept():
    out = {
        "top_view.smd_2t.0": [["m1", "m2"]],
        "top_view.smd_2t.1": [["m1", "m2", "body"]],
    }
    assert suppress_contained_matches(out) == {
        "top_view.smd_2t.1": [["m1", "m2", "body"]],
    }


def test_mask_only_location_with_no_body_survives():
    # smd_2t.0 fires on two locations: one has a body (masks subsumed), one
    # is mask-only (no body template fired) → mask-only instance survives.
    out = {
        "top_view.smd_2t.0": [["m1", "m2"], ["m3", "m4"]],
        "top_view.smd_2t.1": [["m1", "m2", "body"]],
    }
    assert suppress_contained_matches(out) == {
        "top_view.smd_2t.0": [["m3", "m4"]],
        "top_view.smd_2t.1": [["m1", "m2", "body"]],
    }


def test_disjoint_instances_both_kept():
    out = {"top_view.smd_2t.0": [["a", "b"], ["c", "d"]]}
    res = suppress_contained_matches(out)
    assert res == out
    assert res is out  # no removals → same object returned


def test_partial_overlap_keeps_both():
    out = {"top_view.smd_2t.0": [["a", "b", "c"], ["c", "d", "e"]]}
    assert suppress_contained_matches(out) == out


def test_identical_sets_collapse_to_earliest_idx():
    out = {
        "top_view.smd_2t.0": [["a", "b"]],
        "top_view.smd_2t.1": [["a", "b"]],
    }
    # Equal handle sets → keep the earliest template index (0), drop idx 1.
    assert suppress_contained_matches(out) == {
        "top_view.smd_2t.0": [["a", "b"]],
    }


def test_containment_chain_keeps_only_largest():
    out = {
        "top_view.smd_2t.0": [["a"]],
        "top_view.smd_2t.1": [["a", "b"]],
        "top_view.smd_2t.2": [["a", "b", "c"]],
    }
    assert suppress_contained_matches(out) == {
        "top_view.smd_2t.2": [["a", "b", "c"]],
    }


# ---- Scope ----------------------------------------------------------------

def test_same_class_suppressed_across_view_prefixes():
    # Partial under top_view, fuller under bottom_view (e.g. bbox centres
    # straddled a region boundary) — still pooled by class and suppressed;
    # the surviving superset keeps its own key/prefix.
    out = {
        "top_view.smd_2t.0": [["m1", "m2"]],
        "bottom_view.smd_2t.1": [["m1", "m2", "body"]],
    }
    assert suppress_contained_matches(out) == {
        "bottom_view.smd_2t.1": [["m1", "m2", "body"]],
    }


def test_cross_class_never_suppressed():
    # A FiducialCircle handle sits inside a different class's multi-entity
    # match — different classes are never compared.
    out = {
        "top_view.fiducial_circle.0": [["c1"]],
        "top_view.smd_2t.0": [["c1", "r1", "r2"]],
    }
    assert suppress_contained_matches(out) == out


# ---- Flag -----------------------------------------------------------------

def test_disabled_flag_is_passthrough(monkeypatch):
    monkeypatch.setattr(sr, "CONTAINED_SUPPRESSION_ENABLED", False)
    out = {
        "top_view.smd_2t.0": [["m1", "m2"]],
        "top_view.smd_2t.1": [["m1", "m2", "body"]],
    }
    res = sr.suppress_contained_matches(out)
    assert res is out  # untouched, same object


def test_enabled_by_default():
    assert sr.CONTAINED_SUPPRESSION_ENABLED is True


# ---- Determinism & invariants --------------------------------------------

def test_repeated_runs_identical():
    out = {
        "top_view.smd_2t.0": [["m1", "m2"], ["m3", "m4"]],
        "bottom_view.smd_2t.1": [["m1", "m2", "body"]],
        "top_view.smd_3t.0": [["x", "y"]],
        "top_view.smd_3t.1": [["x", "y", "z"]],
    }
    r1 = suppress_contained_matches(out)
    r2 = suppress_contained_matches(out)
    assert r1 == r2


def test_per_class_union_invariant_under_suppression():
    # D4: scan-all / prematch collapse to a per-class handle union, which
    # suppression leaves unchanged (every dropped subset handle is already in
    # the surviving superset of the same class).
    out = {
        "top_view.smd_2t.0": [["m1", "m2"], ["m3", "m4"]],
        "top_view.smd_2t.1": [["m1", "m2", "body"]],
    }
    before = _union_by_class(out)
    after = _union_by_class(suppress_contained_matches(out))
    assert after == before
    assert before["smd_2t"] == {"m1", "m2", "m3", "m4", "body"}


# ---- Defensive: unparseable keys & empty instances ------------------------

def test_unparseable_keys_pass_through():
    out = {
        "garbage": [["x"]],          # no trailing idx → unparseable
        "top_view.smd_2t.0": [["a", "b"]],
    }
    res = suppress_contained_matches(out)
    assert res == out


def test_empty_handle_instance_is_kept():
    out = {
        "top_view.smd_2t.0": [[], ["a", "b"]],
        "top_view.smd_2t.1": [["a", "b", "c"]],
    }
    # [] is skipped from pooling (kept in place); ["a","b"] ⊊ ["a","b","c"]
    # is removed.
    assert suppress_contained_matches(out) == {
        "top_view.smd_2t.0": [[]],
        "top_view.smd_2t.1": [["a", "b", "c"]],
    }
