"""End-to-end tests for POST /api/files/{id}/match-json with the
class-view constraint filter and skip-when-impossible optimisation.

These exercise the wiring inside ``save_match_json``: that the display
ID is threaded through ``split_matches_by_side`` and that the loop
short-circuits when a constrained class's allowed view rects are all
``None``. The filter semantics themselves are covered by
``test_side_regions.py``; this file just confirms the orchestration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app.library import LIBRARIES, Template
from app.matching import EntityShape, MatchResult


@dataclass
class _FakeFindResult:
    """Stand-in for matching.find_matches_from_pointsets()'s return."""
    matches: list[MatchResult]


def _register_file_with_rects(
    monkeypatch, file_id, *, top, bottom, side, product_id="test-product",
):
    """Register a stub file in READY status with the given view
    rectangles. ``save_match_json`` requires ``status == ready_to_match``
    via ``_resolve_file``.

    The file is bound to ``product_id`` so the matcher can see
    product-scoped templates (C4Ball, BGABall, …) committed under the
    same synthetic product id by the test fixtures.
    """
    from app.files import FILE_STORE, READY
    from fastapi.testclient import TestClient
    from app.main import app

    FILE_STORE.register(
        file_id, f"{file_id}.dxf", 1,
        initial_status=READY, product_id=product_id,
    )
    with TestClient(app) as client:
        r = client.patch(
            f"/api/files/{file_id}/side-regions",
            json={
                "top_view_rect": top,
                "bottom_view_rect": bottom,
                "side_view_rect": side,
            },
        )
        assert r.status_code == 200, r.text


def _shape(handle, pts):
    return EntityShape.from_points(handle, pts)


def _mr(handles):
    return MatchResult(handles=handles, score=0.0, scale=1.0)


def _install_fakes(monkeypatch, shapes_by_handle, matches_per_class,
                   tmp_path=None):
    """Monkey-patch the surface that both `app.main` (scan-all path) and
    `app.jobs._save_match_worker` (Save Match path) call into.

    The Save Match endpoint now hands the per-template matching loop to
    `_save_match_worker`, which does its own `from app.matching import
    find_matches_from_pointsets` etc. inside the function body — so the
    worker picks up monkeypatches on those source modules at call time.
    Scan-all still goes through `app.main`, so we patch both bindings
    in one place.

    The worker also reads `parsed/{file_id}.json` from disk and builds
    shapes from it. We stub that out by pointing `app.jobs.parsed_path`
    at a temp file holding `{"primitives": []}` and patching
    `build_handle_index` / `build_entity_shapes` to ignore the parsed
    payload and return the test fixture directly.

    Returns a list that's appended to on every call to the matcher;
    callers can inspect to assert what got invoked (skip-when-impossible).
    """
    call_log: list[str] = []

    def fake_shapes_for(_file_id):
        # Return ({} handle_index, shapes_dict). _resolve_file checks the
        # parsed file exists on disk — we patch that out too below.
        return {}, dict(shapes_by_handle)

    def fake_find(template_point_sets, shapes, *, entity_kinds=None,
                  strategy="chamfer", bbox_ratio=None):
        # The caller doesn't pass the class name in, but we use closure
        # context to bind a class label per template via a small registry.
        # In practice each test installs a fresh _install_fakes for its
        # one library setup.
        cls = _CURRENT_CLASS_NAME[0]
        call_log.append(cls)
        return _FakeFindResult(matches=list(matches_per_class.get(cls, [])))

    import app.main
    import app.matching
    import app.library
    import app.jobs
    # `app.main`: scan-all surface (unchanged).
    monkeypatch.setattr(app.main, "_shapes_for", fake_shapes_for)
    monkeypatch.setattr(app.main, "find_matches_from_pointsets", fake_find)
    # `app.matching` / `app.library`: the worker's inside-function imports
    # resolve against the source module at call time.
    monkeypatch.setattr(app.matching, "find_matches_from_pointsets",
                        fake_find)
    monkeypatch.setattr(app.library, "build_handle_index",
                        lambda _prims: {})
    monkeypatch.setattr(app.matching, "build_entity_shapes",
                        lambda _prims, _hi: dict(shapes_by_handle))
    # `_save_match_worker` reads templates via `Store.load_library`
    # directly (NOT through `LIBRARIES`, which would hit a stale
    # per-process cache). Wrap the returned templates_by_class dict so
    # the fake matcher's class-name side channel still fires on every
    # per-class iteration. This keeps the test fixture aligned with
    # both the old `lib.templates_of` shape and the new fresh-store
    # shape used by the production worker.
    _orig_load = app.library.Store.load_library

    def _wrapped_load(self, library_id, *, product_id=None):
        classes, configs, templates = _orig_load(
            self, library_id, product_id=product_id
        )

        class _TrackingDict(dict):
            def get(_self, key, default=None):
                _CURRENT_CLASS_NAME[0] = key
                return dict.get(_self, key, default)
        return classes, configs, _TrackingDict(templates)

    monkeypatch.setattr(app.library.Store, "load_library", _wrapped_load)
    if tmp_path is not None:
        stub = tmp_path / "stub_parsed.json"
        stub.write_text('{"primitives": []}')
        monkeypatch.setattr(app.jobs, "parsed_path", lambda _file_id: stub)
        # `app.main.save_match_json` pre-flight also calls parsed_path —
        # its binding is the import-time `from app.storage import …`, so
        # patch the `app.main` namespace too.
        monkeypatch.setattr(app.main, "parsed_path", lambda _file_id: stub)
    return call_log


# A tiny mutable cell used by the fake matcher to know which class is
# currently being iterated — driven by the outer iteration over
# lib.classes inside save_match_json. The matcher gets called once per
# template per class, so we update this from a monkey-patched lib hook.
_CURRENT_CLASS_NAME: list[str] = [""]


def _wrap_templates_of(lib):
    """Wrap lib.templates_of so we capture which class is being asked
    for. This lets the fake matcher (which doesn't receive the class
    name) know what to return."""
    orig = lib.templates_of

    def wrapped(cls_name):
        _CURRENT_CLASS_NAME[0] = cls_name
        return orig(cls_name)
    lib.templates_of = wrapped


def _make_lib_with_constrained_templates(monkeypatch, product_id="test-product"):
    """Create a fresh library with one C4Ball and one BGABall template,
    plus one SMD-2T (unconstrained) for control. C4Ball and BGABall
    are product-scoped, so their templates are persisted under the
    given ``product_id`` — the test file in `_register_file_with_rects`
    binds to the same product so the matcher's `load_library` call
    surfaces them. Returns library_id."""
    lib = LIBRARIES.create("test-constraints")
    # Each template is just a single-point cloud; the matcher is faked
    # so the geometry doesn't matter.
    for cls in ("C4Ball", "BGABall", "SMD-2T"):
        t = Template.from_entities(cls, [[(0.0, 0.0), (1.0, 0.0)]])
        lib.add_template_for_file(t, product_id=product_id)
    _wrap_templates_of(lib)
    return lib.library_id


def _make_lib_with_arbitration_classes(monkeypatch, product_id="test-product"):
    """A library that has BOTH BGABall and FiducialCircle templates so
    arbitration has both members populated. BGABall is product-scoped
    so it's persisted under ``product_id``; FiducialCircle is
    library-scoped so its persisted row has product_id IS NULL."""
    lib = LIBRARIES.create("test-arbitration")
    for cls in ("BGABall", "FiducialCircle"):
        t = Template.from_entities(cls, [[(0.0, 0.0), (1.0, 0.0)]])
        lib.add_template_for_file(t, product_id=product_id)
    _wrap_templates_of(lib)
    return lib.library_id


def _bind_file_to_lib(file_id, library_id):
    """Use FILE_STORE.update_library directly (avoids PATCH's
    re-preprocess side effect)."""
    from app.files import FILE_STORE
    FILE_STORE.update_library(file_id, library_id)


@pytest.fixture(autouse=True)
def _reset_class_name():
    _CURRENT_CLASS_NAME[0] = ""
    yield
    _CURRENT_CLASS_NAME[0] = ""


def test_c4ball_outside_top_view_is_dropped(monkeypatch, tmp_path):
    """A C4Ball match landing in bottom_view SHALL be dropped from
    match-JSON, and side_counts['dropped'] SHALL include it."""
    from app.jobs import _save_match_worker
    from app.storage import match_path

    fid = "mjc-1-c4-outside-top"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom={"x0": 50, "y0": 50, "x1": 60, "y1": 60},
        side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    shapes = {
        "TC4": _shape("TC4", [(5.0, 5.0)]),       # inside top_view  → C4Ball OK here
        "BC4": _shape("BC4", [(55.0, 55.0)]),     # inside bottom    → C4Ball dropped here
        "BB":  _shape("BB",  [(55.0, 55.0)]),     # inside bottom    → BGABall OK
        "TB":  _shape("TB",  [(5.0, 5.0)]),       # inside top_view  → BGABall dropped
    }
    matches = {
        "C4Ball":  [_mr(["TC4"]), _mr(["BC4"])],
        "BGABall": [_mr(["BB"]), _mr(["TB"])],
        "SMD-2T":  [],
    }
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    result = _save_match_worker(fid, str(match_path(fid)))

    # Two matches survive (1 C4Ball in top, 1 BGABall in bottom), two dropped.
    assert result["side_counts"]["dropped"] == 2
    assert result["side_counts"]["top_view"] == 1
    assert result["side_counts"]["bottom_view"] == 1

    saved = json.loads(match_path(fid).read_text())
    # Surviving keys only.
    assert "top_view.c4_ball.0" in saved
    assert "bottom_view.bga_ball.0" in saved
    # Dropped pairs do not appear.
    assert not any(k.startswith("bottom_view.c4_ball") for k in saved)
    assert not any(k.startswith("top_view.bga_ball") for k in saved)


def test_c4ball_with_no_top_view_rect_triggers_skip(monkeypatch, tmp_path):
    """When top_view_rect is None, save_match_json SHALL not invoke
    the matcher for C4Ball templates at all (skip-when-impossible)."""
    from app.jobs import _save_match_worker
    from app.storage import match_path

    fid = "mjc-2-no-top-rect"
    _register_file_with_rects(
        monkeypatch, fid,
        top=None,
        bottom={"x0": 50, "y0": 50, "x1": 60, "y1": 60},
        side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    shapes = {"BB": _shape("BB", [(55.0, 55.0)])}
    matches = {
        "C4Ball":  [_mr(["irrelevant"])],   # would be all-dropped if invoked
        "BGABall": [_mr(["BB"])],
        "SMD-2T":  [],
    }
    call_log = _install_fakes(monkeypatch, shapes, matches, tmp_path)

    _save_match_worker(fid, str(match_path(fid)))

    # The matcher MUST NOT have been called for C4Ball — skip-when-impossible.
    assert "C4Ball" not in call_log, (
        f"find_matches_from_pointsets called for C4Ball despite no top_view "
        f"rect; call_log={call_log}"
    )
    # BGABall has a valid rect (bottom_view), so it WAS called.
    assert "BGABall" in call_log

    saved = json.loads(match_path(fid).read_text())
    # No c4_ball key under any prefix.
    assert not any("c4_ball" in k for k in saved)
    # BGABall in bottom_view survives.
    assert "bottom_view.bga_ball.0" in saved


def test_save_match_json_resolves_bga_fiducial_by_view(monkeypatch, tmp_path):
    """End-to-end: BGABall/FiducialCircle cross-fire on identical circle
    geometry resolves to one class per handle via mutually exclusive view
    constraints (BGABall=bottom, FiducialCircle=top) — no density arbitration.
    The worker reports an empty arbitration_counts (registry retired)."""
    from app.jobs import _save_match_worker
    from app.storage import match_path

    fid = "mjc-3-arbitration"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": -50, "y0": -50, "x1": -10, "y1": -10},   # corner region
        bottom={"x0": 0, "y0": 0, "x1": 10, "y1": 10},       # BGA grid region
        side=None,
    )
    library_id = _make_lib_with_arbitration_classes(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    # Build a 3×3 BGA grid (pitch 1) inside bottom_view, plus 2 isolated
    # corner fiducials inside top_view. Both BGABall and FiducialCircle
    # templates cross-fire on every circle.
    grid_handles = [f"g{i}_{j}" for i in range(3) for j in range(3)]
    grid_coords = [(float(i), float(j)) for i in range(3) for j in range(3)]
    fid_handles = ["f0", "f1"]
    fid_coords = [(-30.0, -30.0), (-20.0, -20.0)]

    shapes = {}
    for h, (x, y) in zip(grid_handles, grid_coords):
        shapes[h] = _shape(h, [(x, y)])
    for h, (x, y) in zip(fid_handles, fid_coords):
        shapes[h] = _shape(h, [(x, y)])

    # Both matchers return both grid+fid handles to simulate cross-fire.
    all_matches = [_mr([h]) for h in grid_handles + fid_handles]
    matches = {
        "BGABall":        list(all_matches),
        "FiducialCircle": list(all_matches),
    }
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    result = _save_match_worker(fid, str(match_path(fid)))

    # Density arbitration is retired — the registry is empty, so the worker
    # reports no arbitration_counts. The BGABall/FiducialCircle cross-fire is
    # instead resolved by the mutually exclusive view constraints applied in
    # split_matches_by_side: BGABall-on-fids (top) and FiducialCircle-on-grid
    # (bottom) are both dropped, leaving grid→bga_ball, fids→fiducial_circle.
    assert "arbitration_counts" in result
    assert result["arbitration_counts"] == {}

    saved = json.loads(match_path(fid).read_text())
    # Grid handles end up under bga_ball; fid handles end up under
    # fiducial_circle. No handle appears under both.
    bga_handles_out: set[str] = set()
    fid_handles_out: set[str] = set()
    for k, hls in saved.items():
        for hl in hls:
            if "bga_ball" in k:
                bga_handles_out.update(hl)
            elif "fiducial_circle" in k:
                fid_handles_out.update(hl)
    assert bga_handles_out == set(grid_handles)
    assert fid_handles_out == set(fid_handles)
    assert bga_handles_out.isdisjoint(fid_handles_out)


def test_scan_all_applies_arbitration_to_bga_fiducial_crossfire(monkeypatch):
    """`GET /api/files/{file_id}/scan-all` SHALL apply the same
    arbitration pipeline `save_match_json` uses, so the overlay's
    per-class colouring matches what Save Match would persist.

    Regression test: without this, when BGABall + FiducialCircle
    templates cross-fire on identical circle geometry, the overlay
    paints every BGA ball with the FiducialCircle colour even though
    the saved match.json correctly arbitrates them to BGABall.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    fid = "scan-arb-1"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": -50, "y0": -50, "x1": -10, "y1": -10},
        bottom={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        side=None,
    )
    library_id = _make_lib_with_arbitration_classes(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    # 3×3 BGA grid inside bottom_view + 2 isolated fiducials in top.
    # Same fixture shape as the save_match_json arbitration test above.
    grid_handles = [f"g{i}_{j}" for i in range(3) for j in range(3)]
    grid_coords = [(float(i), float(j)) for i in range(3) for j in range(3)]
    fid_handles = ["f0", "f1"]
    fid_coords = [(-30.0, -30.0), (-20.0, -20.0)]

    shapes = {}
    for h, (x, y) in zip(grid_handles, grid_coords):
        shapes[h] = _shape(h, [(x, y)])
    for h, (x, y) in zip(fid_handles, fid_coords):
        shapes[h] = _shape(h, [(x, y)])

    # Both matchers return all handles to simulate the cross-fire that
    # arbitration exists to resolve.
    all_matches = [_mr([h]) for h in grid_handles + fid_handles]
    matches = {
        "BGABall":        list(all_matches),
        "FiducialCircle": list(all_matches),
    }
    _install_fakes(monkeypatch, shapes, matches)

    with TestClient(app) as client:
        r = client.get(f"/api/files/{fid}/scan-all")
        assert r.status_code == 200, r.text
        body = r.json()

    # Response shape unchanged: {by_class: {<display_name>: [handle, ...]}, total: N}.
    assert set(body.keys()) == {"by_class", "total"}
    by_class = body["by_class"]

    # Grid handles arbitrated to BGABall (≥ MinNeighbors(2)), fiducials
    # to FiducialCircle (≤ MaxNeighbors(1)). No cross-fire residue.
    assert set(by_class.get("BGABall", [])) == set(grid_handles), (
        f"every grid ball should appear in BGABall after arbitration. "
        f"got BGABall={by_class.get('BGABall', [])}"
    )
    assert set(by_class.get("FiducialCircle", [])) == set(fid_handles), (
        f"only the 2 isolated fiducials should appear in FiducialCircle. "
        f"got FiducialCircle={by_class.get('FiducialCircle', [])}"
    )
    # No handle leaks across classes.
    bga_set = set(by_class.get("BGABall", []))
    fid_set = set(by_class.get("FiducialCircle", []))
    assert bga_set.isdisjoint(fid_set)
    # Total accounts for every handle exactly once.
    assert body["total"] == len(grid_handles) + len(fid_handles)


def test_scan_all_matches_save_match_json_class_assignment(monkeypatch,
                                                            tmp_path):
    """End-to-end consistency: scan-all's per-handle class assignment
    SHALL be identical to what save_match_json persists. This locks in
    the single-source-of-truth contract that the new arbitration
    pipeline establishes."""
    from fastapi.testclient import TestClient
    from app.jobs import _save_match_worker
    from app.main import app
    from app.storage import match_path

    fid = "scan-arb-2-parity"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": -50, "y0": -50, "x1": -10, "y1": -10},
        bottom={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        side=None,
    )
    library_id = _make_lib_with_arbitration_classes(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    grid_handles = [f"g{i}_{j}" for i in range(3) for j in range(3)]
    grid_coords = [(float(i), float(j)) for i in range(3) for j in range(3)]
    fid_handles = ["f0", "f1"]
    fid_coords = [(-30.0, -30.0), (-20.0, -20.0)]

    shapes = {}
    for h, (x, y) in zip(grid_handles, grid_coords):
        shapes[h] = _shape(h, [(x, y)])
    for h, (x, y) in zip(fid_handles, fid_coords):
        shapes[h] = _shape(h, [(x, y)])

    all_matches = [_mr([h]) for h in grid_handles + fid_handles]
    matches = {
        "BGABall":        list(all_matches),
        "FiducialCircle": list(all_matches),
    }
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    with TestClient(app) as client:
        scan = client.get(f"/api/files/{fid}/scan-all").json()
    _save_match_worker(fid, str(match_path(fid)))

    saved = json.loads(match_path(fid).read_text())

    # Build the same handle→class mapping from each side.
    scan_assignment = {
        h: cls
        for cls, handles in scan["by_class"].items()
        for h in handles
    }

    save_assignment: dict[str, str] = {}
    snake_to_display = {"bga_ball": "BGABall", "fiducial_circle": "FiducialCircle"}
    for key, hls in saved.items():
        # Keys are like "bottom_view.bga_ball.0".
        parts = key.split(".")
        # Find the class-snake portion (between optional view prefix and trailing idx).
        cls_snake = parts[-2] if len(parts) >= 2 else parts[0]
        cls_display = snake_to_display.get(cls_snake, cls_snake)
        for hl in hls:
            for h in hl:
                save_assignment[h] = cls_display

    assert scan_assignment == save_assignment, (
        f"scan-all and save_match_json must agree on every handle's class.\n"
        f"scan-only handles: {set(scan_assignment) - set(save_assignment)}\n"
        f"save-only handles: {set(save_assignment) - set(scan_assignment)}\n"
        f"disagreements: {{h: (scan_assignment.get(h), save_assignment.get(h)) "
        f"for h in scan_assignment if scan_assignment[h] != save_assignment.get(h)}}"
    )


# ---- Async endpoint behaviour ---------------------------------------------
# These exercise the 202-submit / worker / done-callback wiring that
# `POST /api/files/{file_id}/match-json` now uses. They do not patch the
# executor (it stays real) — instead they drive the worker + callback
# directly to keep the test in-process, since the real ProcessPool can't
# see our monkeypatches.

def test_save_match_post_returns_202_and_registers_job(monkeypatch, tmp_path):
    """POST returns 202 carrying a `job_id` + `file_id`, and the
    in-memory job dict carries a `save_match` entry for that id."""
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    fid = "mjc-post-202"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)
    _install_fakes(monkeypatch, {}, {}, tmp_path)

    with TestClient(app) as client:
        r = client.post(f"/api/files/{fid}/match-json")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["file_id"] == fid
    job_id = body["job_id"]
    assert isinstance(job_id, str) and len(job_id) > 0
    assert job_id in jobs._jobs
    entry = jobs._jobs[job_id]
    assert entry["kind"] == "save_match"
    assert entry["file_id"] == fid
    # Status will be one of {queued, running, done, error} depending on
    # the worker's progress against the real executor. We only assert
    # the entry exists and has the right kind — completion semantics
    # are tested below via direct worker + callback drive.


def test_save_match_done_callback_flips_flag_and_stores_result(
    monkeypatch, tmp_path
):
    """Calling `_save_match_worker` + `_on_save_match_done` end-to-end
    SHALL set `_jobs[job_id]["status"]` to "done", populate `result` with
    the documented shape, flip `FILE_STORE.set_match_saved(file_id, True)`,
    and leave `data/match/{file_id}.json` on disk."""
    import time
    from concurrent.futures import Future
    from app import jobs
    from app.files import FILE_STORE
    from app.storage import match_path

    fid = "mjc-lifecycle-done"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    shapes = {"TC4": _shape("TC4", [(5.0, 5.0)])}
    matches = {"C4Ball": [_mr(["TC4"])], "BGABall": [], "SMD-2T": []}
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    # Pre-condition: file is freshly registered, match_saved is False.
    assert FILE_STORE.get(fid).match_saved is False

    result = jobs._save_match_worker(fid, str(match_path(fid)))

    # Synthesize the job dict + Future that submit_save_match would have
    # produced, then drive the done-callback the way the executor's
    # add_done_callback hook would.
    job_id = "test-lifecycle-job"
    jobs._jobs[job_id] = {
        "id": job_id, "file_id": fid, "kind": "save_match",
        "status": "running", "submitted_at": time.time(),
        "started_at": time.time(), "completed_at": None, "error": None,
    }
    fut: Future = Future()
    fut.set_result(result)
    jobs._on_save_match_done(job_id, fut)

    entry = jobs._jobs[job_id]
    assert entry["status"] == "done"
    assert entry["error"] is None
    assert entry["completed_at"] is not None
    r = entry["result"]
    assert r["file_id"] == fid
    assert r["library_id"] == library_id
    assert "template_keys" in r
    assert "total_matches" in r
    assert "side_counts" in r
    assert "arbitration_counts" in r
    assert "saved_to" in r
    assert r["match_saved"] is True

    assert FILE_STORE.get(fid).match_saved is True
    assert match_path(fid).exists()


def test_save_match_done_callback_does_not_flip_flag_on_worker_error(
    monkeypatch, tmp_path
):
    """If the worker raises, `_on_save_match_done` SHALL set
    `status=error`, populate `error`, leave `match_saved` False, and
    keep the rule-check submit gate honest about the missing role."""
    import time
    from concurrent.futures import Future
    from app import jobs
    from app.files import FILE_STORE

    fid = "mjc-lifecycle-error"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)
    # Workers never actually run here; we just stage the job entry +
    # an already-failed Future and drive the callback.

    assert FILE_STORE.get(fid).match_saved is False

    job_id = "test-lifecycle-error-job"
    jobs._jobs[job_id] = {
        "id": job_id, "file_id": fid, "kind": "save_match",
        "status": "running", "submitted_at": time.time(),
        "started_at": time.time(), "completed_at": None, "error": None,
    }
    fut: Future = Future()
    fut.set_exception(RuntimeError("simulated worker crash"))
    jobs._on_save_match_done(job_id, fut)

    entry = jobs._jobs[job_id]
    assert entry["status"] == "error"
    assert isinstance(entry["error"], str) and entry["error"]
    assert "simulated worker crash" in entry["error"]
    assert entry["completed_at"] is not None
    assert "result" not in entry
    # match_saved stays False on worker error → rule-check submit gate
    # (which checks this flag) keeps rejecting the role. The gate's
    # behaviour itself is covered by tests/test_products.py.
    assert FILE_STORE.get(fid).match_saved is False


def test_save_match_post_with_missing_parsed_file_returns_synchronous_error(
    monkeypatch, tmp_path
):
    """Pre-flight: if `parsed/{file_id}.json` is missing on disk, the
    POST handler SHALL return a synchronous 4xx/5xx and NOT register a
    job."""
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    fid = "mjc-preflight-missing-parsed"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)
    # Deliberately do NOT install fakes — we want `parsed_path(fid)` to
    # point at a real non-existent file under DATA_DIR.

    jobs_before = set(jobs._jobs)
    with TestClient(app) as client:
        r = client.post(f"/api/files/{fid}/match-json")
    assert r.status_code >= 400 and r.status_code != 202, r.text
    # No new entry registered in the in-memory job dict.
    assert set(jobs._jobs) == jobs_before


# ---- Regression: worker does NOT use LIBRARIES (stale cache bug) -------
#
# A production bug surfaced: in the async-save-match flow, the worker
# subprocess called `LIBRARIES.get(library_id)` which returned a stale
# per-process `Library` cache. Templates added in the FastAPI parent
# process after the worker first cached the library never landed in
# the worker's view, so save_match produced an empty (or partial)
# match JSON. The fix bypasses LIBRARIES entirely in the worker —
# `_save_match_worker` reads templates from a fresh `Store.load_library`
# call every job (mirroring `_preprocess_worker`'s existing pattern).
# This test pins that contract: poisoning `LIBRARIES.get` to fail
# must NOT break `_save_match_worker`.

def test_save_match_worker_does_not_use_libraries_cache(monkeypatch, tmp_path):
    """`_save_match_worker` SHALL NOT call `LIBRARIES.get(...)`. The
    worker reads templates via `Store.load_library` so it always sees
    the latest committed templates, regardless of cache staleness."""
    from app.jobs import _save_match_worker
    from app.storage import match_path
    import app.library

    fid = "no-libraries-cache"
    _register_file_with_rects(
        monkeypatch, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    library_id = _make_lib_with_constrained_templates(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    shapes = {"TC4": _shape("TC4", [(5.0, 5.0)])}
    matches = {"C4Ball": [_mr(["TC4"])], "BGABall": [], "SMD-2T": []}
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    # Poison LIBRARIES.get — if the worker calls it, the test fails.
    libraries_get_calls: list[str] = []

    def boom(library_id):
        libraries_get_calls.append(library_id)
        raise RuntimeError(
            f"LIBRARIES.get({library_id!r}) called from worker — the "
            f"worker should use Store.load_library directly"
        )

    monkeypatch.setattr(app.library.LIBRARIES, "get", boom)

    result = _save_match_worker(fid, str(match_path(fid)))

    assert libraries_get_calls == [], (
        f"worker called LIBRARIES.get; this caches per-process and goes "
        f"stale across save_match jobs. Calls: {libraries_get_calls}"
    )
    # Worker still produced a valid result despite the poisoned cache,
    # which proves it read templates from the fresh-store path.
    assert "top_view.c4_ball.0" in result["template_keys"]
    assert result["total_matches"] >= 1


# ---- Regression: preprocess prematch JSON is post-arbitration ----------
#
# The class-toolbar count chip the viewer renders on first file open
# reads `data.by_class[cls].length` from `/api/files/{id}/prematch`,
# which is the JSON `_preprocess_worker` writes. Before the fix that
# accompanies this test, the preprocess loop unioned handles per class
# WITHOUT running `arbitrate(...)`, so cross-firing classes (e.g.
# BGABall + FiducialCircle on identical circle geometry) each listed
# every handle. The viewer reported `FiducialCircle ×17486` even when
# arbitration assigns 17483 of those handles to BGABall and only 3 to
# FiducialCircle. This test pins the fix: prematch JSON's by_class
# counts match what `_save_match_worker` produces.

def test_preprocess_prematch_clean_when_radii_differ(
    monkeypatch, tmp_path,
):
    """With BGABall and FiducialCircle templates of DIFFERENT radii there is
    no matcher cross-fire (each template matches only its own circles), so the
    prematch JSON's by-class counts are naturally clean — grid→BGABall,
    fids→FiducialCircle — with no density arbitration needed.

    (The density arbitration that used to resolve *same-radius* cross-fire at
    prematch is retired in favour of view-based disambiguation, which applies
    at save-match/scan-all once side regions are drawn. Same-radius cross-fire
    at prematch — before any rect exists — is no longer auto-resolved; the
    deployment relies on distinct radii or on the save-match view split.)"""
    import json as _json
    from app.jobs import _preprocess_worker, _save_match_worker
    from app.storage import match_path

    fid = "prematch-arb"
    _register_file_with_rects(
        monkeypatch, fid,
        # Side regions left null on purpose — preprocess runs at
        # upload time, BEFORE the operator draws side regions. The
        # arbitration logic must still resolve cross-fire without
        # depending on view prefixes.
        top=None, bottom=None, side=None,
    )
    library_id = _make_lib_with_arbitration_classes(monkeypatch)
    _bind_file_to_lib(fid, library_id)

    # Same 3×3 grid + 2 fiducials fixture as the existing scan-all
    # parity test — known-good arbitration outcome.
    grid_handles = [f"g{i}_{j}" for i in range(3) for j in range(3)]
    grid_coords = [(float(i), float(j)) for i in range(3) for j in range(3)]
    fid_handles = ["f0", "f1"]
    fid_coords = [(-30.0, -30.0), (-20.0, -20.0)]

    shapes = {}
    for h, (x, y) in zip(grid_handles, grid_coords):
        shapes[h] = _shape(h, [(x, y)])
    for h, (x, y) in zip(fid_handles, fid_coords):
        shapes[h] = _shape(h, [(x, y)])
    # Distinct radii → no cross-fire: BGABall matches only the grid balls,
    # FiducialCircle matches only the isolated fiducials.
    matches = {
        "BGABall":        [_mr([h]) for h in grid_handles],
        "FiducialCircle": [_mr([h]) for h in fid_handles],
    }
    call_log = _install_fakes(monkeypatch, shapes, matches, tmp_path)

    # Drive `_preprocess_worker` directly. The transient-primitives
    # cache short-circuits the heavy `flatten_for_render` call, and
    # `_install_fakes` already patches the shape-build + matcher
    # surface inside the worker.
    transient = tmp_path / "transient.json"
    transient.write_text(_json.dumps({
        "primitives": [],
        "bbox": [0.0, 0.0, 10.0, 10.0],
        "background": "#ffffff",
        "insunits": 4,
        "applied_scale": 1.0,
    }))
    parsed_dst = tmp_path / "parsed.json"
    prematch_dst = tmp_path / "prematch.json"
    _preprocess_worker(
        fid, src="(unused)",
        parsed_dst=str(parsed_dst),
        prematch_dst=str(prematch_dst),
        library_id=library_id,
        selected_layers=None,
        transient_primitives=str(transient),
        dev_overrides_snapshot=None,
        user_unit_override=None,
        # Same synthetic product the file fixture binds to and the
        # library fixture persisted BGABall templates under.
        product_id="test-product",
    )
    # call_log captured for future assertions; not directly asserted on
    # because the iteration order over `lib.classes` is the same in
    # production and the by_class output already pins the outcome.
    assert "BGABall" in call_log and "FiducialCircle" in call_log

    pm = _json.loads(prematch_dst.read_text())
    by_class = pm["by_class"]

    # With distinct radii there is no cross-fire, so by_class is clean
    # straight out of matching: the 9 grid balls under BGABall, the 2
    # isolated fiducials under FiducialCircle — no shared handles.
    assert set(by_class.get("BGABall", [])) == set(grid_handles), (
        f"BGABall should hold the 9 grid handles after arbitration; "
        f"got {by_class.get('BGABall', [])}"
    )
    assert set(by_class.get("FiducialCircle", [])) == set(fid_handles), (
        f"FiducialCircle should hold only the 2 isolated fiducials "
        f"after arbitration; got {by_class.get('FiducialCircle', [])}"
    )
    # No handle leaks across classes — cross-fire is fully resolved.
    bga_set = set(by_class.get("BGABall", []))
    fid_set = set(by_class.get("FiducialCircle", []))
    assert bga_set.isdisjoint(fid_set)
    # Total accounts for every handle exactly once — pre-fix this
    # would have been ~22 (11 handles × 2 cross-firing classes).
    assert pm["total"] == len(grid_handles) + len(fid_handles)

    # NOTE: we deliberately do NOT compare against `_save_match_worker`
    # here. Save Match applies view-constraint skip-when-impossible
    # (BGABall is constrained to bottom/side view; with no rects drawn
    # BGABall is skipped entirely), while preprocess runs at upload
    # time BEFORE side regions exist. Both apply the same `arbitrate`
    # against `CLASS_ARBITRATION_GROUPS` — they just feed it different
    # pre-arbitration sets. The user-visible win this test pins is
    # that prematch's by_class counts are no longer the raw
    # cross-fire union.
