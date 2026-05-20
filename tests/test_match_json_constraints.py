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


def _register_file_with_rects(monkeypatch, file_id, *, top, bottom, side):
    """Register a stub file in READY status with the given view
    rectangles. ``save_match_json`` requires ``status == ready_to_match``
    via ``_resolve_file``."""
    from app.files import FILE_STORE, READY
    from fastapi.testclient import TestClient
    from app.main import app

    FILE_STORE.register(file_id, f"{file_id}.dxf", 1, initial_status=READY)
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


def _install_fakes(monkeypatch, shapes_by_handle, matches_per_class):
    """Monkey-patch ``app.main._shapes_for`` to return a fixed shapes
    dict, and ``app.main.find_matches_from_pointsets`` to return canned
    matches keyed by the calling class's display ID.

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
    monkeypatch.setattr(app.main, "_shapes_for", fake_shapes_for)
    monkeypatch.setattr(app.main, "find_matches_from_pointsets", fake_find)
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


def _make_lib_with_constrained_templates(monkeypatch):
    """Create a fresh library with one C4Ball and one BGABall template,
    plus one SMD-2T (unconstrained) for control. Returns library_id."""
    lib = LIBRARIES.create("test-constraints")
    # Each template is just a single-point cloud; the matcher is faked
    # so the geometry doesn't matter.
    for cls in ("C4Ball", "BGABall", "SMD-2T"):
        t = Template.from_entities(cls, [[(0.0, 0.0), (1.0, 0.0)]])
        lib.add_template(t)
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


def test_c4ball_outside_top_view_is_dropped(monkeypatch):
    """A C4Ball match landing in bottom_view SHALL be dropped from
    match-JSON, and side_counts['dropped'] SHALL include it."""
    from fastapi.testclient import TestClient
    from app.main import app
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
    _install_fakes(monkeypatch, shapes, matches)

    with TestClient(app) as client:
        r = client.post(f"/api/files/{fid}/match-json")
        assert r.status_code == 200, r.text
        body = r.json()

    # Two matches survive (1 C4Ball in top, 1 BGABall in bottom), two dropped.
    assert body["side_counts"]["dropped"] == 2
    assert body["side_counts"]["top_view"] == 1
    assert body["side_counts"]["bottom_view"] == 1

    saved = json.loads(match_path(fid).read_text())
    # Surviving keys only.
    assert "top_view.c4_ball.0" in saved
    assert "bottom_view.bga_ball.0" in saved
    # Dropped pairs do not appear.
    assert not any(k.startswith("bottom_view.c4_ball") for k in saved)
    assert not any(k.startswith("top_view.bga_ball") for k in saved)


def test_c4ball_with_no_top_view_rect_triggers_skip(monkeypatch):
    """When top_view_rect is None, save_match_json SHALL not invoke
    the matcher for C4Ball templates at all (skip-when-impossible)."""
    from fastapi.testclient import TestClient
    from app.main import app
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
    call_log = _install_fakes(monkeypatch, shapes, matches)

    with TestClient(app) as client:
        r = client.post(f"/api/files/{fid}/match-json")
        assert r.status_code == 200, r.text

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
