"""End-to-end tests for POST /api/files/{id}/match-json with the
class-view constraint filter and skip-when-impossible optimisation.

These exercise the wiring inside the save-match path: that the display
ID is threaded through ``split_matches_by_side`` and that the loop
short-circuits when a constrained class's allowed view rects are all
``None``. The filter semantics themselves are covered by
``test_side_regions.py``; this file just confirms the orchestration.

Versioned model: every test creates a real product+version (the worker
resolves the version's library via a fresh VersionStore read), binds the
stub file into that version, and passes ``version_id`` everywhere.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest

from app.library import LIBRARIES, Template
from app.matching import EntityShape, MatchResult


@dataclass
class _FakeFindResult:
    """Stand-in for matching.find_matches_from_pointsets()'s return."""
    matches: list[MatchResult]


def _new_version(tag: str):
    """A real product + version (the save-match worker reads the version
    row fresh from SQLite, so it must exist)."""
    from app.versions import VERSION_STORE
    _, version = VERSION_STORE.create_product(
        f"mjc-{tag}-{uuid.uuid4().hex[:6]}", "v1"
    )
    return version


def _register_file_with_rects(
    version_id, file_id, *, top, bottom, side,
):
    """Bind a stub file in READY status into the version with the given
    view rectangles. The save-match endpoint requires
    ``status == ready_to_match`` via ``_resolve_file``."""
    from app.files import FILE_STORE, READY
    from fastapi.testclient import TestClient
    from app.main import app

    FILE_STORE.register_content(file_id, f"{file_id}.dxf", 1)
    FILE_STORE.bind(version_id, "BD", file_id, initial_status=READY)
    with TestClient(app) as client:
        r = client.patch(
            f"/api/files/{file_id}/side-regions",
            params={"version_id": version_id},
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

    The worker does its own `from app.matching import …` inside the
    function body — so it picks up monkeypatches on those source modules
    at call time. Scan-all still goes through `app.main`, so we patch
    both bindings in one place.

    The worker also reads `parsed/{version_id}/{file_id}.json` from disk
    and builds shapes from it. We stub that out by pointing
    `app.jobs.parsed_path` at a temp file holding `{"primitives": []}`
    and patching `build_handle_index` / `build_entity_shapes` to ignore
    the parsed payload and return the test fixture directly.

    Returns a list that's appended to on every call to the matcher;
    callers can inspect to assert what got invoked (skip-when-impossible).
    """
    call_log: list[str] = []

    def fake_shapes_for(_version_id, _file_id):
        return {}, dict(shapes_by_handle)

    def fake_find(template_point_sets, shapes, *, entity_kinds=None,
                  strategy="chamfer", bbox_ratio=None):
        cls = _CURRENT_CLASS_NAME[0]
        call_log.append(cls)
        return _FakeFindResult(matches=list(matches_per_class.get(cls, [])))

    import app.main
    import app.matching
    import app.library
    import app.jobs
    # `app.main`: scan-all surface.
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
    # per-class iteration.
    _orig_load = app.library.Store.load_library

    def _wrapped_load(self, library_id):
        classes, configs, templates = _orig_load(self, library_id)

        class _TrackingDict(dict):
            def get(_self, key, default=None):
                _CURRENT_CLASS_NAME[0] = key
                return dict.get(_self, key, default)
        return classes, configs, _TrackingDict(templates)

    monkeypatch.setattr(app.library.Store, "load_library", _wrapped_load)
    if tmp_path is not None:
        stub = tmp_path / "stub_parsed.json"
        stub.write_text('{"primitives": []}')
        monkeypatch.setattr(app.jobs, "parsed_path", lambda *_a: stub)
        # `app.main.save_match_json` pre-flight also calls parsed_path —
        # its binding is the import-time `from app.storage import …`, so
        # patch the `app.main` namespace too.
        monkeypatch.setattr(app.main, "parsed_path", lambda *_a: stub)
    return call_log


# A tiny mutable cell used by the fake matcher to know which class is
# currently being iterated — driven by the outer iteration over
# the classes list. The matcher gets called once per template per class,
# so we update this from the wrapped templates dict's .get hook.
_CURRENT_CLASS_NAME: list[str] = [""]


def _make_version_with_constrained_templates(tag="constraints"):
    """A fresh product+version whose library holds one C4Ball, one
    BGABall (view-constrained) and one SMD-2T (unconstrained, control)
    template. Returns the Version."""
    version = _new_version(tag)
    lib = LIBRARIES.get(version.library_id)
    for cls in ("C4Ball", "BGABall", "SMD-2T"):
        t = Template.from_entities(cls, [[(0.0, 0.0), (1.0, 0.0)]])
        lib.add_template_for_file(t)
    return version


def _make_version_with_arbitration_classes(tag="arbitration"):
    """A version whose library has BOTH BGABall and FiducialCircle
    templates so the view-split disambiguation has both members."""
    version = _new_version(tag)
    lib = LIBRARIES.get(version.library_id)
    for cls in ("BGABall", "FiducialCircle"):
        t = Template.from_entities(cls, [[(0.0, 0.0), (1.0, 0.0)]])
        lib.add_template_for_file(t)
    return version


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
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom={"x0": 50, "y0": 50, "x1": 60, "y1": 60},
        side=None,
    )

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

    result = _save_match_worker(version.id, fid, str(match_path(version.id, fid)))

    # Two matches survive (1 C4Ball in top, 1 BGABall in bottom), two dropped.
    assert result["side_counts"]["dropped"] == 2
    assert result["side_counts"]["top_view"] == 1
    assert result["side_counts"]["bottom_view"] == 1

    saved = json.loads(match_path(version.id, fid).read_text())
    # Surviving keys only.
    assert "top_view.c4_ball.0" in saved
    assert "bottom_view.bga_ball.0" in saved
    # Dropped pairs do not appear.
    assert not any(k.startswith("bottom_view.c4_ball") for k in saved)
    assert not any(k.startswith("top_view.bga_ball") for k in saved)


def test_c4ball_with_no_top_view_rect_triggers_skip(monkeypatch, tmp_path):
    """When top_view_rect is None, the save-match worker SHALL not invoke
    the matcher for C4Ball templates at all (skip-when-impossible)."""
    from app.jobs import _save_match_worker
    from app.storage import match_path

    fid = "mjc-2-no-top-rect"
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top=None,
        bottom={"x0": 50, "y0": 50, "x1": 60, "y1": 60},
        side=None,
    )

    shapes = {"BB": _shape("BB", [(55.0, 55.0)])}
    matches = {
        "C4Ball":  [_mr(["irrelevant"])],   # would be all-dropped if invoked
        "BGABall": [_mr(["BB"])],
        "SMD-2T":  [],
    }
    call_log = _install_fakes(monkeypatch, shapes, matches, tmp_path)

    _save_match_worker(version.id, fid, str(match_path(version.id, fid)))

    # The matcher MUST NOT have been called for C4Ball — skip-when-impossible.
    assert "C4Ball" not in call_log, (
        f"find_matches_from_pointsets called for C4Ball despite no top_view "
        f"rect; call_log={call_log}"
    )
    # BGABall has a valid rect (bottom_view), so it WAS called.
    assert "BGABall" in call_log

    saved = json.loads(match_path(version.id, fid).read_text())
    # No c4_ball key under any prefix.
    assert not any("c4_ball" in k for k in saved)
    # BGABall in bottom_view survives.
    assert "bottom_view.bga_ball.0" in saved


def test_save_match_json_resolves_bga_fiducial_by_view(monkeypatch, tmp_path):
    """End-to-end: BGABall/FiducialCircle cross-fire on identical circle
    geometry resolves to one class per handle via mutually exclusive view
    constraints (BGABall=bottom, FiducialCircle=top) — no density arbitration
    (the subsystem was removed)."""
    from app.jobs import _save_match_worker
    from app.storage import match_path

    fid = "mjc-3-arbitration"
    version = _make_version_with_arbitration_classes()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": -50, "y0": -50, "x1": -10, "y1": -10},   # corner region
        bottom={"x0": 0, "y0": 0, "x1": 10, "y1": 10},       # BGA grid region
        side=None,
    )

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

    _save_match_worker(version.id, fid, str(match_path(version.id, fid)))

    # Density arbitration is retired. The BGABall/FiducialCircle cross-fire is
    # resolved by the mutually exclusive view constraints applied in
    # split_matches_by_side: BGABall-on-fids (top) and FiducialCircle-on-grid
    # (bottom) are both dropped, leaving grid→bga_ball, fids→fiducial_circle.
    saved = json.loads(match_path(version.id, fid).read_text())
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
    arbitration pipeline the save-match worker uses, so the overlay's
    per-class colouring matches what Save Match would persist."""
    from fastapi.testclient import TestClient
    from app.main import app

    fid = "scan-arb-1"
    version = _make_version_with_arbitration_classes()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": -50, "y0": -50, "x1": -10, "y1": -10},
        bottom={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        side=None,
    )

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
    _install_fakes(monkeypatch, shapes, matches)

    with TestClient(app) as client:
        r = client.get(
            f"/api/files/{fid}/scan-all", params={"version_id": version.id}
        )
        assert r.status_code == 200, r.text
        body = r.json()

    assert set(body.keys()) == {"by_class", "total"}
    by_class = body["by_class"]

    assert set(by_class.get("BGABall", [])) == set(grid_handles), (
        f"every grid ball should appear in BGABall after arbitration. "
        f"got BGABall={by_class.get('BGABall', [])}"
    )
    assert set(by_class.get("FiducialCircle", [])) == set(fid_handles), (
        f"only the 2 isolated fiducials should appear in FiducialCircle. "
        f"got FiducialCircle={by_class.get('FiducialCircle', [])}"
    )
    bga_set = set(by_class.get("BGABall", []))
    fid_set = set(by_class.get("FiducialCircle", []))
    assert bga_set.isdisjoint(fid_set)
    assert body["total"] == len(grid_handles) + len(fid_handles)


def test_scan_all_matches_save_match_json_class_assignment(monkeypatch,
                                                            tmp_path):
    """End-to-end consistency: scan-all's per-handle class assignment
    SHALL be identical to what the save-match worker persists."""
    from fastapi.testclient import TestClient
    from app.jobs import _save_match_worker
    from app.main import app
    from app.storage import match_path

    fid = "scan-arb-2-parity"
    version = _make_version_with_arbitration_classes()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": -50, "y0": -50, "x1": -10, "y1": -10},
        bottom={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        side=None,
    )

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
        scan = client.get(
            f"/api/files/{fid}/scan-all", params={"version_id": version.id}
        ).json()
    _save_match_worker(version.id, fid, str(match_path(version.id, fid)))

    saved = json.loads(match_path(version.id, fid).read_text())

    scan_assignment = {
        h: cls
        for cls, handles in scan["by_class"].items()
        for h in handles
    }

    save_assignment: dict[str, str] = {}
    snake_to_display = {"bga_ball": "BGABall", "fiducial_circle": "FiducialCircle"}
    for key, hls in saved.items():
        parts = key.split(".")
        cls_snake = parts[-2] if len(parts) >= 2 else parts[0]
        cls_display = snake_to_display.get(cls_snake, cls_snake)
        for hl in hls:
            for h in hl:
                save_assignment[h] = cls_display

    assert scan_assignment == save_assignment, (
        f"scan-all and save_match must agree on every handle's class.\n"
        f"scan-only handles: {set(scan_assignment) - set(save_assignment)}\n"
        f"save-only handles: {set(save_assignment) - set(scan_assignment)}"
    )


# ---- Async endpoint behaviour ---------------------------------------------
# These exercise the 202-submit / worker / done-callback wiring that
# `POST /api/files/{file_id}/match-json` uses.

def test_save_match_post_returns_202_and_registers_job(monkeypatch, tmp_path):
    """POST returns 202 carrying a `job_id` + `file_id`, and the
    in-memory job dict carries a `save_match` entry for that id."""
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    fid = "mjc-post-202"
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    _install_fakes(monkeypatch, {}, {}, tmp_path)

    with TestClient(app) as client:
        r = client.post(
            f"/api/files/{fid}/match-json", params={"version_id": version.id}
        )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["file_id"] == fid
    assert body["version_id"] == version.id
    job_id = body["job_id"]
    assert isinstance(job_id, str) and len(job_id) > 0
    assert job_id in jobs._jobs
    entry = jobs._jobs[job_id]
    assert entry["kind"] == "save_match"
    assert entry["file_id"] == fid
    assert entry["version_id"] == version.id


def test_save_match_done_callback_flips_flag_and_stores_result(
    monkeypatch, tmp_path
):
    """Calling `_save_match_worker` + `_on_save_match_done` end-to-end
    SHALL set the job to "done", populate `result` with the documented
    shape, flip `match_saved` on the binding, and leave the
    version-scoped match JSON on disk."""
    import time
    from concurrent.futures import Future
    from app import jobs
    from app.files import FILE_STORE
    from app.storage import match_path

    fid = "mjc-lifecycle-done"
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )

    shapes = {"TC4": _shape("TC4", [(5.0, 5.0)])}
    matches = {"C4Ball": [_mr(["TC4"])], "BGABall": [], "SMD-2T": []}
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    # Pre-condition: binding is freshly created, match_saved is False.
    assert FILE_STORE.get(version.id, fid).match_saved is False

    result = jobs._save_match_worker(
        version.id, fid, str(match_path(version.id, fid))
    )

    # Synthesize the job dict + Future that submit_save_match would have
    # produced, then drive the done-callback.
    job_id = "test-lifecycle-job"
    jobs._jobs[job_id] = {
        "id": job_id, "version_id": version.id, "file_id": fid,
        "kind": "save_match",
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
    assert r["version_id"] == version.id
    assert r["library_id"] == version.library_id
    assert "template_keys" in r
    assert "total_matches" in r
    assert "side_counts" in r
    assert "saved_to" in r
    assert r["match_saved"] is True

    assert FILE_STORE.get(version.id, fid).match_saved is True
    assert match_path(version.id, fid).exists()


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
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    # Workers never actually run here; we just stage the job entry +
    # an already-failed Future and drive the callback.

    assert FILE_STORE.get(version.id, fid).match_saved is False

    job_id = "test-lifecycle-error-job"
    jobs._jobs[job_id] = {
        "id": job_id, "version_id": version.id, "file_id": fid,
        "kind": "save_match",
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
    # (which checks this flag) keeps rejecting the role.
    assert FILE_STORE.get(version.id, fid).match_saved is False


def test_save_match_post_with_missing_parsed_file_returns_synchronous_error(
    monkeypatch, tmp_path
):
    """Pre-flight: if `parsed/{version_id}/{file_id}.json` is missing on
    disk, the POST handler SHALL return a synchronous 4xx/5xx and NOT
    register a job."""
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    fid = "mjc-preflight-missing-parsed"
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )
    # Deliberately do NOT install fakes — we want the real parsed_path
    # to point at a non-existent file under DATA_DIR.

    def _save_match_jobs():
        return {
            j for j, v in jobs._jobs.items()
            if v.get("kind") == "save_match"
        }

    with TestClient(app) as client:
        save_match_before = _save_match_jobs()
        r = client.post(
            f"/api/files/{fid}/match-json", params={"version_id": version.id}
        )
        save_match_after = _save_match_jobs()
    assert r.status_code >= 400 and r.status_code != 202, r.text
    assert save_match_after == save_match_before


# ---- Regression: worker does NOT use LIBRARIES (stale cache bug) -------

def test_save_match_worker_does_not_use_libraries_cache(monkeypatch, tmp_path):
    """`_save_match_worker` SHALL NOT call `LIBRARIES.get(...)`. The
    worker reads templates via `Store.load_library` so it always sees
    the latest committed templates, regardless of cache staleness."""
    from app.jobs import _save_match_worker
    from app.storage import match_path
    import app.library

    fid = "no-libraries-cache"
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )

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

    result = _save_match_worker(
        version.id, fid, str(match_path(version.id, fid))
    )

    assert libraries_get_calls == [], (
        f"worker called LIBRARIES.get; this caches per-process and goes "
        f"stale across save_match jobs. Calls: {libraries_get_calls}"
    )
    assert "top_view.c4_ball.0" in result["template_keys"]
    assert result["total_matches"] >= 1


# ---- Regression: preprocess prematch JSON is post-arbitration ----------

def test_preprocess_prematch_clean_when_radii_differ(
    monkeypatch, tmp_path,
):
    """With BGABall and FiducialCircle templates of DIFFERENT radii there is
    no matcher cross-fire (each template matches only its own circles), so the
    prematch JSON's by-class counts are naturally clean — grid→BGABall,
    fids→FiducialCircle — with no density arbitration needed."""
    import json as _json
    from app.jobs import _preprocess_worker

    fid = "prematch-arb"
    version = _make_version_with_arbitration_classes()
    _register_file_with_rects(
        version.id, fid,
        # Side regions left null on purpose — preprocess runs at
        # upload time, BEFORE the operator draws side regions.
        top=None, bottom=None, side=None,
    )

    grid_handles = [f"g{i}_{j}" for i in range(3) for j in range(3)]
    grid_coords = [(float(i), float(j)) for i in range(3) for j in range(3)]
    fid_handles = ["f0", "f1"]
    fid_coords = [(-30.0, -30.0), (-20.0, -20.0)]

    shapes = {}
    for h, (x, y) in zip(grid_handles, grid_coords):
        shapes[h] = _shape(h, [(x, y)])
    for h, (x, y) in zip(fid_handles, fid_coords):
        shapes[h] = _shape(h, [(x, y)])
    # Distinct radii → no cross-fire.
    matches = {
        "BGABall":        [_mr([h]) for h in grid_handles],
        "FiducialCircle": [_mr([h]) for h in fid_handles],
    }
    call_log = _install_fakes(monkeypatch, shapes, matches, tmp_path)

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
        version.id, fid, src="(unused)",
        parsed_dst=str(parsed_dst),
        prematch_dst=str(prematch_dst),
        library_id=version.library_id,
        selected_layers=None,
        transient_primitives=str(transient),
        dev_overrides_snapshot=None,
        user_unit_override=None,
    )
    assert "BGABall" in call_log and "FiducialCircle" in call_log

    pm = _json.loads(prematch_dst.read_text())
    by_class = pm["by_class"]

    assert set(by_class.get("BGABall", [])) == set(grid_handles)
    assert set(by_class.get("FiducialCircle", [])) == set(fid_handles)
    bga_set = set(by_class.get("BGABall", []))
    fid_set = set(by_class.get("FiducialCircle", []))
    assert bga_set.isdisjoint(fid_set)
    assert pm["total"] == len(grid_handles) + len(fid_handles)


# ---- Regression: Save Match refreshes the stale pre-match snapshot ----------

def test_save_match_worker_refreshes_prematch_snapshot(monkeypatch, tmp_path):
    """`_save_match_worker` SHALL rewrite the version-scoped prematch
    JSON from its live scan, so a class whose template was committed
    AFTER preprocess appears in the auto-shown overlay on the next
    viewer load — instead of only after a manual Scan All."""
    from app.jobs import _save_match_worker
    from app.storage import match_path, prematch_path

    fid = "save-refreshes-prematch"
    version = _make_version_with_constrained_templates()
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        bottom=None, side=None,
    )

    # Simulate the stale preprocess-time snapshot.
    pm_path = prematch_path(version.id, fid)
    pm_path.parent.mkdir(parents=True, exist_ok=True)
    pm_path.write_text(json.dumps({"by_class": {}, "total": 0}))

    shapes = {
        "s1": _shape("s1", [(5.0, 5.0)]),
        "s2": _shape("s2", [(6.0, 5.0)]),
    }
    matches = {"C4Ball": [], "BGABall": [], "SMD-2T": [_mr(["s1"]), _mr(["s2"])]}
    _install_fakes(monkeypatch, shapes, matches, tmp_path)

    _save_match_worker(version.id, fid, str(match_path(version.id, fid)))

    refreshed = json.loads(pm_path.read_text())
    assert set(refreshed["by_class"].get("SMD-2T", [])) == {"s1", "s2"}
    assert refreshed["total"] == 2
    assert "C4Ball" not in refreshed["by_class"]
    assert "BGABall" not in refreshed["by_class"]


# ---- Contained-match suppression -------------------------------------------

def test_save_match_worker_suppresses_contained_smd(monkeypatch, tmp_path):
    """Mask-only (idx 0) + mask+body (idx 1) templates of the SAME class: on a
    body location the mask-only instance (handle subset) is dropped from the
    written Match JSON; a mask-only-only location (no body) survives. The
    reported counts satisfy `total = survivors + dropped + suppressed`."""
    from app.jobs import _save_match_worker
    from app.storage import match_path
    from app.library import LIBRARIES, Template
    import app.matching
    import app.library
    import app.jobs

    fid = "smd-contained-1"
    version = _new_version("contained")
    lib = LIBRARIES.get(version.library_id)
    # Two DISTINCT template geometries so the library stores both (identical
    # geometry would dedupe to one). The fake matcher ignores geometry and
    # returns matches by call order, so idx 0 = mask-only, idx 1 = mask+body.
    geoms = [
        [[(0.0, 0.0), (1.0, 0.0)]],                  # idx 0: mask-only stand-in
        [[(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)]],      # idx 1: mask+body stand-in
    ]
    for g in geoms:
        t = Template.from_entities("SMD-2T", g)
        lib.add_template_for_file(t)
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
        bottom=None, side=None,
    )

    shapes = {
        "m1": _shape("m1", [(10.0, 10.0)]),
        "m2": _shape("m2", [(11.0, 10.0)]),
        "body": _shape("body", [(10.5, 10.0)]),
        "m3": _shape("m3", [(50.0, 50.0)]),
        "m4": _shape("m4", [(51.0, 50.0)]),
    }
    per_idx = {
        0: [_mr(["m1", "m2"]), _mr(["m3", "m4"])],
        1: [_mr(["m1", "m2", "body"])],
    }
    call_i = {"n": 0}

    def fake_find(tps, shp, *, entity_kinds=None, strategy="chamfer",
                  bbox_ratio=None):
        i = call_i["n"]
        call_i["n"] += 1
        return _FakeFindResult(matches=list(per_idx.get(i, [])))

    monkeypatch.setattr(app.matching, "find_matches_from_pointsets", fake_find)
    monkeypatch.setattr(app.library, "build_handle_index", lambda _p: {})
    monkeypatch.setattr(app.matching, "build_entity_shapes",
                        lambda _p, _hi: dict(shapes))
    stub = tmp_path / "stub_parsed.json"
    stub.write_text('{"primitives": []}')
    monkeypatch.setattr(app.jobs, "parsed_path", lambda *_a: stub)

    result = _save_match_worker(
        version.id, fid, str(match_path(version.id, fid))
    )
    saved = json.loads(match_path(version.id, fid).read_text())

    # The body location's fuller instance remains; its mask-only twin is gone.
    assert saved.get("top_view.smd_2t.1") == [["m1", "m2", "body"]]
    # The mask-only-only location survives under idx 0.
    assert saved.get("top_view.smd_2t.0") == [["m3", "m4"]]
    # No standalone {m1, m2} instance anywhere in the file.
    all_instances = [hl for hls in saved.values() for hl in hls]
    assert ["m1", "m2"] not in all_instances
    # Each physical handle recorded exactly once.
    flat = sorted(h for hl in all_instances for h in hl)
    assert flat == ["body", "m1", "m2", "m3", "m4"]

    # Counts + invariant.
    assert result["suppressed_count"] == 1
    assert result["total_matches"] == 3  # raw: 2 (idx 0) + 1 (idx 1)
    sc = result["side_counts"]
    survivors = (sc["top_view"] + sc["bottom_view"]
                 + sc["side_view"] + sc["unassigned"])
    assert survivors == 2
    assert (result["total_matches"]
            == survivors + sc["dropped"] + result["suppressed_count"])


def test_scan_all_by_class_union_invariant_to_contained_match(monkeypatch):
    """scan-all collapses to a per-class handle UNION, so a contained (subset)
    instance does not change `by_class`. Presence vs absence of the mask-only
    instance yields identical `by_class` — locking the proof that the scan-all
    preview needs no suppression code."""
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.library import LIBRARIES, Template
    import app.main

    fid = "smd-union-invariant"
    version = _new_version("union-invariant")
    lib = LIBRARIES.get(version.library_id)
    geoms = [
        [[(0.0, 0.0), (1.0, 0.0)]],
        [[(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)]],
    ]
    for g in geoms:
        t = Template.from_entities("SMD-2T", g)
        lib.add_template_for_file(t)
    _register_file_with_rects(
        version.id, fid,
        top={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
        bottom=None, side=None,
    )

    shapes = {
        "m1": _shape("m1", [(10.0, 10.0)]),
        "m2": _shape("m2", [(11.0, 10.0)]),
        "body": _shape("body", [(10.5, 10.0)]),
    }
    state = {"per_idx": {}, "i": 0}

    def fake_find(tps, shp, *, entity_kinds=None, strategy="chamfer",
                  bbox_ratio=None):
        i = state["i"]
        state["i"] += 1
        return _FakeFindResult(matches=list(state["per_idx"].get(i, [])))

    monkeypatch.setattr(app.main, "_shapes_for",
                        lambda _v, _f: ({}, dict(shapes)))
    monkeypatch.setattr(app.main, "find_matches_from_pointsets", fake_find)

    def _scan(per_idx):
        state["per_idx"] = per_idx
        state["i"] = 0
        with TestClient(fastapi_app) as client:
            r = client.get(
                f"/api/files/{fid}/scan-all", params={"version_id": version.id}
            )
            assert r.status_code == 200, r.text
            return r.json()

    # PRESENT: mask-only (idx 0) + mask+body (idx 1) both fire.
    present = _scan({0: [_mr(["m1", "m2"])],
                     1: [_mr(["m1", "m2", "body"])]})
    # ABSENT: only the mask+body fires (mask-only instance removed).
    absent = _scan({0: [], 1: [_mr(["m1", "m2", "body"])]})

    assert set(present["by_class"].get("SMD-2T", [])) == {"m1", "m2", "body"}
    assert present["by_class"] == absent["by_class"]
    assert present["total"] == absent["total"]
