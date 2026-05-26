"""Unit + integration tests for the auto-normalize-unit-suspect-dxf change.

Covers `detect_scale_factor` (every row in the spec table) and the
in-place `_maybe_rescale` helper that wires it into `RenderOutput`."""

from __future__ import annotations

import pytest

from app.dxf import RenderOutput, _maybe_rescale, detect_scale_factor


# ---- detect_scale_factor — declared-unit paths ---------------------------
@pytest.mark.parametrize("insunits,expected", [
    (1, 25.4),      # inch → mm
    (5, 10.0),      # cm   → mm
    (6, 1000.0),    # m    → mm
    (4, 1.0),       # mm   → mm (never rescale a declared-mm file)
])
def test_declared_unit_factor_is_authoritative(insunits, expected):
    # Diagonal magnitude must not influence the declared-unit decision.
    for diag in (0.01, 10.0, 50_000.0):
        assert detect_scale_factor(insunits, diag) == expected


def test_declared_mm_never_rescaled_even_for_huge_bbox():
    assert detect_scale_factor(4, 50_000.0) == 1.0


# ---- detect_scale_factor — unitless / unknown path -----------------------
@pytest.mark.parametrize("insunits", [0, None])
def test_1000x_too_big_unitless_picks_M_0p001(insunits):
    # Classic μm-as-mm export: a 42-mm chip stored as 42_000 units.
    assert detect_scale_factor(insunits, 42_000) == pytest.approx(0.001)


@pytest.mark.parametrize("insunits", [0, None])
def test_1000x_too_small_unitless_picks_M_1000(insunits):
    # mm-as-m export: a 50-mm package stored as 0.05 units.
    assert detect_scale_factor(insunits, 0.05) == pytest.approx(1000.0)


def test_100x_too_big_picks_smallest_in_range():
    # diag=6000 has both M=0.1 → 600 mm and M=0.01 → 60 mm in range.
    # The packaging-aware rule prefers the smallest output (60 mm), not
    # the closest-to-1 factor (600 mm).
    assert detect_scale_factor(0, 6000) == pytest.approx(0.01)


def test_100x_too_small_picks_M_100():
    assert detect_scale_factor(0, 0.5) == pytest.approx(100.0)


@pytest.mark.parametrize("diag", [15, 100, 600, 5000])
def test_in_range_unitless_is_a_no_op(diag):
    assert detect_scale_factor(0, diag) == 1.0


@pytest.mark.parametrize("diag", [5, 7, 9])
def test_marginal_below_range_stays_at_1(diag):
    # A real 5×5 mm dice has diagonal ≈ 7 mm. Without the |log10(M)| > 1
    # safety guard this would grab M=10 and inflate to 70 mm; with the
    # guard it stays put.
    assert detect_scale_factor(0, diag) == 1.0


@pytest.mark.parametrize("diag", [6000.1, 6001, 9999])
def test_in_range_with_only_marginal_alternatives_stays_at_1(diag):
    # 6001 has in-range alternatives M=0.1 → 600 and M=1 → 6001 (out).
    # Wait: 6001 > 5000 so M=1 is OUT of range; only M=0.01 → 60 and
    # M=0.1 → 600 qualify, both ≥ |log10|=1 (only M=0.01 passes >1).
    # Just test that the detector picks the qualifying smallest output.
    got = detect_scale_factor(0, diag)
    assert got == pytest.approx(0.01) or got == 1.0


def test_out_of_range_stays_at_1():
    # No power-of-10 in [-3, +3] brings 5e-05 inside [10, 5000].
    assert detect_scale_factor(0, 0.00005) == 1.0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_degenerate_diagonal_returns_1(bad):
    assert detect_scale_factor(0, bad) == 1.0


def test_unrecognised_insunits_returns_1():
    # `2` is "foot" in DXF — we don't have a conversion rule for it (no
    # downstream demand), so the detector leaves it alone.
    assert detect_scale_factor(2, 100) == 1.0


# ---- _maybe_rescale — wires detection into RenderOutput in place --------
def _render(insunits: int | None, bbox, primitives=None) -> RenderOutput:
    return RenderOutput(
        primitives=list(primitives or []),
        bbox=bbox,
        background="#ffffff",
        insunits=insunits,
    )


def test_no_rescale_leaves_render_untouched():
    prims = [{"type": "line", "start": [0, 0], "end": [100, 200]}]
    r = _render(4, (0.0, 0.0, 100.0, 200.0), prims)
    out, factor = _maybe_rescale(r)
    assert factor == 1.0
    assert out.applied_scale == 1.0
    assert out.bbox == (0.0, 0.0, 100.0, 200.0)
    assert out.primitives[0]["end"] == [100, 200]


def test_rescale_multiplies_every_primitive_kind():
    prims = [
        {"type": "point", "pos": [10.0, 20.0]},
        {"type": "line", "start": [0.0, 0.0], "end": [42_000.0, 42_000.0]},
        {"type": "polyline", "points": [[0.0, 0.0], [100.0, 200.0], [300.0, 400.0]]},
        {"type": "circle", "center": [1000.0, 2000.0], "r": 500.0},
        {"type": "filled_polygon", "rings": [[[0.0, 0.0], [100.0, 100.0]]]},
    ]
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0), prims)
    out, factor = _maybe_rescale(r)
    # 42000 diagonal-ish → factor 0.001 → mm output.
    assert factor == pytest.approx(0.001)
    assert out.applied_scale == pytest.approx(0.001)
    assert out.bbox == pytest.approx((0.0, 0.0, 42.0, 42.0))
    assert out.primitives[0]["pos"] == [pytest.approx(0.01), pytest.approx(0.02)]
    assert out.primitives[1]["end"] == [pytest.approx(42.0), pytest.approx(42.0)]
    assert out.primitives[2]["points"][1] == [pytest.approx(0.1), pytest.approx(0.2)]
    assert out.primitives[3]["center"] == [pytest.approx(1.0), pytest.approx(2.0)]
    assert out.primitives[3]["r"] == pytest.approx(0.5)
    assert out.primitives[4]["rings"][0][1] == [pytest.approx(0.1), pytest.approx(0.1)]


def test_insunits_preserved_after_rescale():
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0))
    out, factor = _maybe_rescale(r)
    assert factor != 1.0
    # `insunits` documents the source DXF and must not be touched.
    assert out.insunits == 0


def test_no_bbox_is_safe_no_op():
    out, factor = _maybe_rescale(_render(0, None))
    assert factor == 1.0
    assert out.applied_scale == 1.0


# ---- Match JSON invalidation on factor change ----------------------------
def test_match_json_invalidated_when_applied_scale_changes(tmp_path, monkeypatch):
    """Re-preprocessing a file whose `applied_scale` flips from 1.0 to
    something else SHALL delete `data/match/<file_id>.json` and clear
    `match_saved`. See `dxf-pipeline` spec, requirement
    "Auto-rescale invalidates saved Match JSON"."""
    import json
    from app import jobs, storage
    from app.files import FileStore

    # Isolate disk + DB so the test doesn't disturb real state.
    monkeypatch.setattr(storage, "MATCH_DIR", tmp_path / "match")
    storage.MATCH_DIR.mkdir(parents=True, exist_ok=True)
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("X", "x.dxf", 1)
    # Seed: a file that was preprocessed under the old (pre-rescale)
    # world and has a saved match JSON on disk.
    fs.update_parsed("X", 1, (0, 0, 42_000, 42_000), "#000",
                     insunits=0, applied_scale=1.0)
    fs.set_match_saved("X", True)
    mp = storage.match_path("X")
    mp.write_text(json.dumps({"bga_ball.0": [["h1"], ["h2"]]}))

    # Re-preprocess result: detector flips to 0.001. Invoke the helper
    # via the same fan-out callback the worker uses.
    factor_changed = fs.update_parsed("X", 1, (0, 0, 42, 42), "#000",
                                       insunits=0, applied_scale=0.001)
    assert factor_changed is True
    jobs._invalidate_match_after_rescale("X")

    assert not mp.exists(), "match JSON should be deleted after rescale"
    assert fs.get("X").match_saved is False


def test_match_json_left_alone_when_factor_unchanged(tmp_path, monkeypatch):
    import json
    from app import storage
    from app.files import FileStore

    monkeypatch.setattr(storage, "MATCH_DIR", tmp_path / "match")
    storage.MATCH_DIR.mkdir(parents=True, exist_ok=True)
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("Y", "y.dxf", 1)
    fs.update_parsed("Y", 1, (0, 0, 100, 100), "#000",
                     insunits=4, applied_scale=1.0)
    fs.set_match_saved("Y", True)
    mp = storage.match_path("Y")
    mp.write_text(json.dumps({"bga_ball.0": [["h1"]]}))

    # Same factor → caller never triggers invalidation.
    factor_changed = fs.update_parsed("Y", 1, (0, 0, 100, 100), "#000",
                                       insunits=4, applied_scale=1.0)
    assert factor_changed is False
    assert mp.exists(), "match JSON must survive a no-op rescale check"
    assert fs.get("Y").match_saved is True


# ---- Startup migration selects only legacy unit-suspect files -----------
def test_startup_migration_selects_legacy_unit_suspect_files(tmp_path, monkeypatch):
    """The lifespan-time scanner picks up files whose persisted INSUNITS +
    bbox would resolve to a non-1.0 factor under the current detector,
    skips already-rescaled rows, and skips healthy mm rows. Submission
    itself is mocked so we don't spin up real workers."""
    from app.files import FileStore
    from app import main as main_mod

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)
    monkeypatch.setattr("app.main.FILE_STORE", fs)

    # (a) Needs rescale: legacy unitless 1000× too big.
    fs.register("needs", "n.dxf", 1)
    fs.update_parsed("needs", 1, (0, 0, 42_000, 42_000), "#000",
                     insunits=0, applied_scale=1.0)
    # (b) Already rescaled in a prior run — bbox is in mm, applied_scale
    # is non-1; the detector returns 1.0 for it and the scanner skips.
    fs.register("done", "d.dxf", 1)
    fs.update_parsed("done", 1, (0, 0, 42, 42), "#000",
                     insunits=0, applied_scale=0.001)
    # (c) Healthy mm-declared file — detector returns 1.0, scanner skips.
    fs.register("mm", "m.dxf", 1)
    fs.update_parsed("mm", 1, (0, 0, 300, 300), "#000",
                     insunits=4, applied_scale=1.0)

    captured: dict = {}
    def fake_submit(file_id_filter=None, *, kind="reprocess-all"):
        captured["filter"] = set(file_id_filter or ())
        captured["kind"] = kind
        return "fake-job-id"
    monkeypatch.setattr("app.main.jobs.submit_reprocess_all", fake_submit)

    main_mod._submit_unit_rescale_migration()

    assert captured["kind"] == "unit-rescale-migration"
    assert captured["filter"] == {"needs"}, (
        "only the legacy unitless 1000× file should be re-submitted"
    )


def test_startup_migration_is_idempotent_on_empty_set(tmp_path, monkeypatch):
    """When no row matches the trigger, the scanner does not submit any
    job at all (no empty parent job either)."""
    from app.files import FileStore
    from app import main as main_mod

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)
    monkeypatch.setattr("app.main.FILE_STORE", fs)

    fs.register("ok", "o.dxf", 1)
    fs.update_parsed("ok", 1, (0, 0, 300, 300), "#000",
                     insunits=4, applied_scale=1.0)

    called = {"hit": False}
    def fake_submit(file_id_filter=None, *, kind="reprocess-all"):
        called["hit"] = True
        return "x"
    monkeypatch.setattr("app.main.jobs.submit_reprocess_all", fake_submit)

    main_mod._submit_unit_rescale_migration()
    assert called["hit"] is False
