"""Smoke tests for the FastAPI surface — using TestClient against a temp DB.

Migrated to the product-versioning model (2026-06-10, openspec
add-product-versioning): products carry versions, uploads land on
/api/versions/{vid}/files, and every file-centric endpoint requires
?version_id=.
"""

from __future__ import annotations




# Minimal valid DXF — enough bytes to pass the upload's "non-empty" check
# and the .dxf filename check. The upload pipeline parses asynchronously
# and tolerates malformed bytes by transitioning the file to status=error
# without breaking the upload-time contract we test below.
_STUB_DXF = b"0\nEOF\n"


def _new_version(client, name: str) -> tuple[str, str]:
    """Create a product with its mandatory first version; return (pid, vid)."""
    r = client.post("/api/products", json={"name": name, "version_label": "v1"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["versions"][0]["id"]


def _version_payload(client, pid: str, vid: str) -> dict:
    g = client.get(f"/api/products/{pid}")
    assert g.status_code == 200, g.text
    for v in g.json()["versions"]:
        if v["id"] == vid:
            return v
    raise AssertionError(f"version {vid} not in product {pid}")


def test_classes_endpoint_lists_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("SMDR2_N_JOBS", "1")
    from fastapi.testclient import TestClient

    # Have to import after monkeypatching env to ensure the singletons pick
    # up the right paths. We reuse the real singletons since they share a
    # per-session SQLite under the conftest tmp data dir.
    from app.main import app

    with TestClient(app) as client:
        _, vid = _new_version(client, "classes-defaults")
        r = client.get("/api/classes", params={"version_id": vid})
        assert r.status_code == 200
        classes = r.json()["classes"]
        names = {c["name"] for c in classes}
        # Default classes should be present.
        assert {"SMD-2T", "C4Ball", "BGABall", "Substrate", "FiducialSquare"}.issubset(names)


def test_files_endpoint_returns_a_list():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.get("/api/files")
        assert r.status_code == 200
        assert "files" in r.json()


def test_create_product_requires_version_label():
    """C7: no version-less products — a missing version_label is a 422."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/products", json={"name": "no-label"})
        assert r.status_code == 422


def test_upload_to_version_rejects_non_dxf():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "test-product")
        r = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("a.txt", b"not dxf", "text/plain")},
            data={"dxf_role": "BD"},
        )
        assert r.status_code == 400


def test_match_endpoint_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "match-missing-file")
        r = client.post(
            "/api/files/nonexistent/match",
            json={"handles": ["X"]},
            params={"version_id": vid},
        )
        assert r.status_code == 404


def test_match_swap_endpoint_rejects_empty_pattern():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "swap-empty-pattern")
        r = client.post(
            "/api/files/nonexistent/match-swap",
            json={"pattern_a": [], "pattern_b": ["X"]},
            params={"version_id": vid},
        )
        assert r.status_code == 400


def test_match_swap_endpoint_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "swap-missing-file")
        r = client.post(
            "/api/files/nonexistent/match-swap",
            json={"pattern_a": ["X"], "pattern_b": ["Y"]},
            params={"version_id": vid},
        )
        assert r.status_code == 404


def test_warm_shapes_endpoint_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "warm-missing-file")
        r = client.post(
            "/api/files/nonexistent/warm-shapes", params={"version_id": vid},
        )
        assert r.status_code == 404


# ---- Per-version template storage ----------------------------------------
# The two-tier (library vs. product) template scope died with the
# versioning model: `add_template_for_file` no longer takes a product_id
# and the templates table has no product scope column. The old routing
# tests (test_add_template_for_file_routes_library_scoped /
# test_add_template_for_file_routes_product_scoped) were deleted —
# library CRUD / product-scope removed 2026-06-10, openspec
# add-product-versioning. Isolation is now per-version-library, tested
# below.
def test_templates_isolated_between_version_libraries(tmp_path):
    """Each version owns its library 1:1. A template committed into
    version A's library SHALL NOT be visible in version B's
    Store.load_library view — the scope rule scan-all depends on."""
    from app.library import LibraryRegistry, Store, Template
    from app.versions import VersionStore

    db = tmp_path / "library.sqlite"
    vs = VersionStore(db)
    _, va = vs.create_product("prod-a", "v1")
    _, vb = vs.create_product("prod-b", "v1")
    reg = LibraryRegistry(Store(db))
    lib_a = reg.get(va.library_id)
    reg.get(vb.library_id)  # seed defaults into B's library too

    tmpl = Template.from_entities("SMD-2T", [[(0.0, 0.0), (1.0, 0.0)]])
    lib_a.add_template_for_file(tmpl)

    _, _, a_view = reg.store.load_library(va.library_id)
    assert len(a_view.get("SMD-2T", [])) == 1

    _, _, b_view = reg.store.load_library(vb.library_id)
    assert len(b_view.get("SMD-2T", [])) == 0


def test_commit_on_file_not_bound_to_version_404s():
    """A content row with no binding in the target version cannot commit —
    404 'file not found in this version'. (The old product-scope 400 is
    gone: any class commits into the version's library now.)"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE

    file_id = "orphan-commit-test"
    # Register content only — NO binding into any version.
    FILE_STORE.register_content(file_id, f"{file_id}.dxf", 1)
    with TestClient(app) as client:
        _, vid = _new_version(client, "orphan-commit")
        r = client.post(
            f"/api/files/{file_id}/commit",
            json={"handles": ["A"], "class_name": "Substrate"},
            params={"version_id": vid},
        )
    assert r.status_code == 404, r.text


def _bind_stub(vid: str, fid: str, *, role: str = "BD", status: str | None = None):
    """Register a stub content row and bind it into the version."""
    from app.files import FILE_STORE, PREPROCESSING
    FILE_STORE.register_content(fid, "stub.dxf", 1)
    FILE_STORE.bind(
        vid, role, fid,
        dxf_view="multi", initial_status=status or PREPROCESSING,
    )


def test_side_regions_patch_persists_and_normalises(tmp_path, monkeypatch):
    """PATCH /api/files/{id}/side-regions stores normalised rectangles and
    surfaces them on the next GET. Uses a freshly-bound stub file so we
    don't depend on the test.dxf preprocess pipeline."""
    from fastapi.testclient import TestClient
    from app.main import app

    fid = "side-regions-test-1"
    with TestClient(app) as client:
        _, vid = _new_version(client, "side-regions-1")
        _bind_stub(vid, fid)

        # Send a deliberately unnormalised top_view rect (x0 > x1), a normal
        # bottom_view rect, and a side_view rect.
        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "top_view_rect": {"x0": 10, "y0": 5, "x1": 0, "y1": 0},
                "bottom_view_rect": {"x0": 50, "y0": 50, "x1": 60, "y1": 60},
                "side_view_rect": {"x0": 100, "y0": 100, "x1": 110, "y1": 110},
            },
            params={"version_id": vid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 5.0}
        assert body["bottom_view_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
        assert body["side_view_rect"] == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}
        assert body["match_saved"] is False

        # GET round-trips the rectangles on the binding.
        g = client.get(f"/api/files/{fid}", params={"version_id": vid}).json()
        assert g["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 5.0}
        assert g["bottom_view_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
        assert g["side_view_rect"] == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}


def test_side_regions_patch_clears_saved_match(tmp_path):
    """Editing regions must delete the cached match JSON and clear
    match_saved so the rule-checker won't see stale unprefixed keys."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app.main import app
    from app.storage import match_path

    fid = "side-regions-test-2"
    with TestClient(app) as client:
        _, vid = _new_version(client, "side-regions-2")
        _bind_stub(vid, fid)
        FILE_STORE.set_match_saved(vid, fid, True)
        # Drop a placeholder match cache on disk to mimic a prior Save Match run.
        mp = match_path(vid, fid)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("{\"smd.0\": [[\"A\"]]}")

        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "top_view_rect": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
            params={"version_id": vid},
        )
        assert r.status_code == 200, r.text
        assert r.json()["match_saved"] is False

    assert not mp.exists(), "match cache should be deleted on region edit"
    assert FILE_STORE.get(vid, fid).match_saved is False


def test_side_regions_patch_only_side_view_clears_saved_match(tmp_path):
    """PATCHing with only side_view_rect changing must also invalidate the
    saved Match JSON — the cache-invalidation hook covers all three rects."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app.main import app
    from app.storage import match_path

    fid = "side-regions-test-3"
    with TestClient(app) as client:
        _, vid = _new_version(client, "side-regions-3")
        _bind_stub(vid, fid)
        FILE_STORE.set_match_saved(vid, fid, True)
        mp = match_path(vid, fid)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text("{\"smd.0\": [[\"A\"]]}")

        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "top_view_rect": None,
                "bottom_view_rect": None,
                "side_view_rect": {"x0": 0, "y0": 0, "x1": 5, "y1": 5},
            },
            params={"version_id": vid},
        )
        assert r.status_code == 200, r.text
        assert r.json()["match_saved"] is False
        assert r.json()["side_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 5.0, "y1": 5.0}

    assert not mp.exists()
    assert FILE_STORE.get(vid, fid).match_saved is False


def test_side_regions_patch_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "side-regions-404")
        r = client.patch(
            "/api/files/nonexistent/side-regions",
            json={
                "top_view_rect": None,
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
            params={"version_id": vid},
        )
        assert r.status_code == 404


# ---- multi-DXF-per-role API tests ---------------------------------------
def test_upload_two_files_same_role_coexist():
    """A (version, role) accumulates DXFs; uploading a second file under
    the same role doesn't evict the first when no replace_file_id is sent."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid, vid = _new_version(client, "two-files-coexist")

        r1 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        assert r1.status_code == 200, r1.text

        r2 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        assert r2.status_code == 200, r2.text

        v = _version_payload(client, pid, vid)
        ids = {f["id"] for f in v["files_by_role_all"]["SBT"]}
        assert ids == {r1.json()["file_id"], r2.json()["file_id"]}
        assert len(ids) == 2


def test_upload_with_replace_file_id_evicts_target():
    """`replace_file_id` evicts that specific binding before the new one lands."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid, vid = _new_version(client, "replace-specific")

        r1 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        old_id = r1.json()["file_id"]

        r2 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT", "replace_file_id": old_id},
        )
        assert r2.status_code == 200, r2.text

        v = _version_payload(client, pid, vid)
        ids = [f["id"] for f in v["files_by_role_all"]["SBT"]]
        assert old_id not in ids
        assert ids == [r2.json()["file_id"]]


def test_upload_replace_file_id_must_match_version_and_role():
    """Replacing across versions or roles is rejected so the eviction
    can't cross slot boundaries by accident."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid_a = _new_version(client, "replace-cross-a")
        _, vid_b = _new_version(client, "replace-cross-b")

        r = client.post(
            f"/api/versions/{vid_a}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        fid_a = r.json()["file_id"]

        # Try to replace version A's binding via version B's endpoint.
        cross = client.post(
            f"/api/versions/{vid_b}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT", "replace_file_id": fid_a},
        )
        assert cross.status_code == 400


def test_two_files_can_each_mark_same_view():
    """When two DXFs share a (version, role), each binding may independently
    mark its own top/bottom/side region rectangles. Rule-check merges
    matches across files, so overlapping view labels are expected, not
    a conflict."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "two-files-independent-views")

        r1 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        a_id = r1.json()["file_id"]
        r2 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        b_id = r2.json()["file_id"]

        ra = client.patch(
            f"/api/files/{a_id}/side-regions",
            json={
                "top_view_rect": {"x0": 0, "y0": 0, "x1": 5, "y1": 5},
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
            params={"version_id": vid},
        )
        assert ra.status_code == 200, ra.text

        rb = client.patch(
            f"/api/files/{b_id}/side-regions",
            json={
                "top_view_rect": {"x0": 10, "y0": 10, "x1": 20, "y1": 20},
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
            params={"version_id": vid},
        )
        assert rb.status_code == 200, rb.text

        ga = client.get(f"/api/files/{a_id}", params={"version_id": vid}).json()
        gb = client.get(f"/api/files/{b_id}", params={"version_id": vid}).json()
        assert ga["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 5.0, "y1": 5.0}
        assert gb["top_view_rect"] == {"x0": 10.0, "y0": 10.0, "x1": 20.0, "y1": 20.0}


def test_delete_one_of_many_keeps_siblings():
    """Removing one binding from a multi-file role leaves the rest intact."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid, vid = _new_version(client, "delete-keeps-siblings")
        a = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        b = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        c = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("c.dxf", _STUB_DXF + b"c", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        kept_ids = {a.json()["file_id"], c.json()["file_id"]}
        gone_id = b.json()["file_id"]

        d = client.delete(f"/api/versions/{vid}/files/{gone_id}")
        assert d.status_code == 204

        v = _version_payload(client, pid, vid)
        remaining = {f["id"] for f in v["files_by_role_all"]["SBT"]}
        assert remaining == kept_ids


def test_delete_file_404_when_not_in_version():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "delete-404")
        r = client.delete(f"/api/versions/{vid}/files/no-such-file")
        assert r.status_code == 404


# ---- per-class match strategy API ---------------------------------------
# Per-class strategy now lives on the version's library (1:1), so the old
# /api/libraries/{lib}/classes/... endpoints became
# /api/versions/{vid}/classes/... (library CRUD removed 2026-06-10,
# openspec add-product-versioning).
def test_class_listing_includes_strategy_fields():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.library import CLASS_DEFAULT_MATCH_CONFIG
    with TestClient(app) as client:
        _, vid = _new_version(client, "strategy-listing")
        r = client.get(f"/api/versions/{vid}/classes")
        assert r.status_code == 200
        classes = r.json()["classes"]
        assert classes, "version library should be seeded with default classes"
        for c in classes:
            # Most classes default to chamfer/None; the large-outline classes
            # (Substrate / RingOuter / RingInner) seed as their signature default.
            strat, ratio = CLASS_DEFAULT_MATCH_CONFIG.get(
                c["name"], ("chamfer", None)
            )
            assert c["match_strategy"] == strat
            assert c["bbox_ratio"] == ratio


def test_set_strategy_signature_defaults_bbox_ratio_to_005():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "strategy-default-ratio")
        r = client.put(
            f"/api/versions/{vid}/classes/Substrate/strategy",
            json={"strategy": "signature"},
        )
        assert r.status_code == 200
        assert r.json()["match_strategy"] == "signature"
        assert r.json()["bbox_ratio"] == 0.05
        sub = next(
            c for c in client.get(f"/api/versions/{vid}/classes").json()["classes"]
            if c["name"] == "Substrate"
        )
        assert sub["match_strategy"] == "signature"
        assert sub["bbox_ratio"] == 0.05


def test_set_strategy_signature_with_explicit_bbox_ratio():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "strategy-explicit-ratio")
        r = client.put(
            f"/api/versions/{vid}/classes/Substrate/strategy",
            json={"strategy": "signature", "bbox_ratio": 0.1},
        )
        assert r.status_code == 200
        assert r.json()["bbox_ratio"] == 0.1


def test_flip_back_to_chamfer_clears_bbox_ratio():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "strategy-clear")
        url = f"/api/versions/{vid}/classes/Substrate/strategy"
        client.put(url, json={"strategy": "signature", "bbox_ratio": 0.05})
        r = client.put(url, json={"strategy": "chamfer"})
        assert r.status_code == 200
        assert r.json()["match_strategy"] == "chamfer"
        assert r.json()["bbox_ratio"] is None


def test_set_strategy_rejects_invalid_values():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "strategy-invalid")
        url = f"/api/versions/{vid}/classes/Substrate/strategy"
        # Unknown strategy value
        assert client.put(url, json={"strategy": "fuzzy"}).status_code == 400
        # bbox_ratio out of (0, 1] under signature
        for bad in (0, -0.1, 1.5):
            r = client.put(url, json={"strategy": "signature", "bbox_ratio": bad})
            assert r.status_code == 400, f"bbox_ratio={bad!r} should 400"


def test_set_strategy_unknown_version_or_class_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.put(
            "/api/versions/no-such-version/classes/Substrate/strategy",
            json={"strategy": "signature"},
        )
        assert r.status_code == 404
        _, vid = _new_version(client, "strategy-no-class")
        r2 = client.put(
            f"/api/versions/{vid}/classes/DoesNotExist/strategy",
            json={"strategy": "signature"},
        )
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# RING and LID coexist on the same version
# ---------------------------------------------------------------------------


def test_upload_lid_to_version_with_ring_succeeds():
    """RING and LID are independent roles. Uploading LID after RING
    SHALL succeed and both files SHALL be visible under
    `files_by_role_all`."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid, vid = _new_version(client, "ring-then-lid-coexist")

        r1 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("ring.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "RING"},
        )
        assert r1.status_code == 200, r1.text
        ring_id = r1.json()["file_id"]

        r2 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("lid.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "LID"},
        )
        assert r2.status_code == 200, r2.text
        lid_id = r2.json()["file_id"]

        v = _version_payload(client, pid, vid)
        assert [f["id"] for f in v["files_by_role_all"]["RING"]] == [ring_id]
        assert [f["id"] for f in v["files_by_role_all"]["LID"]]  == [lid_id]


def test_unit_override_endpoint_happy_path(monkeypatch):
    """POST with a recognised unit returns 202, schedules a recompute,
    writes the override to the binding, and reports the affected
    product+version. The job pool is stubbed so no real preprocess fires.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app import jobs

    fid = "unit-override-test-happy"

    submitted = {}
    def fake_submit(version_id, file_id, unit):
        submitted["version_id"] = version_id
        submitted["file_id"] = file_id
        submitted["unit"] = unit
        return "fake-job-1"
    monkeypatch.setattr(jobs, "submit_unit_override_preprocess", fake_submit)

    with TestClient(app) as client:
        pid, vid = _new_version(client, "unit-override-happy")
        _bind_stub(vid, fid)
        r = client.post(
            f"/api/files/{fid}/unit-override",
            json={"unit": "inch"},
            params={"version_id": vid},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["file_id"] == fid
        assert body["version_id"] == vid
        assert body["unit"] == "inch"
        assert body["job_id"] == "fake-job-1"
        # The binding belongs to one (product, version) — that's the
        # affected list (shaped as a list for forward compatibility).
        assert body["affected_products"] == [
            {"id": pid, "name": "unit-override-happy", "version_label": "v1"},
        ]
    assert submitted == {"version_id": vid, "file_id": fid, "unit": "inch"}


def test_unit_override_endpoint_rejects_unknown_unit():
    from fastapi.testclient import TestClient
    from app.main import app

    fid = "unit-override-test-bad-unit"

    with TestClient(app) as client:
        _, vid = _new_version(client, "unit-override-bad-unit")
        _bind_stub(vid, fid)
        r = client.post(
            f"/api/files/{fid}/unit-override",
            json={"unit": "feet"},
            params={"version_id": vid},
        )
        assert r.status_code == 400
        # Binding row was not modified.
        g = client.get(f"/api/files/{fid}", params={"version_id": vid}).json()
        assert g["user_unit_override"] is None


def test_unit_override_endpoint_returns_409_when_job_inflight(monkeypatch):
    """An in-flight preprocess job for the same binding blocks a new
    unit-override POST — the response carries the live job id so the
    viewer can resume polling."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import jobs

    fid = "unit-override-test-409"

    monkeypatch.setattr(jobs, "find_inflight_preprocess_job",
                         lambda version_id, file_id: "inflight-job-xyz")

    with TestClient(app) as client:
        _, vid = _new_version(client, "unit-override-409")
        _bind_stub(vid, fid)
        r = client.post(
            f"/api/files/{fid}/unit-override",
            json={"unit": "mm"},
            params={"version_id": vid},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["job_id"] == "inflight-job-xyz"


def test_unit_override_endpoint_404_on_missing_file():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "unit-override-404")
        r = client.post(
            "/api/files/nonexistent-file-id/unit-override",
            json={"unit": "mm"},
            params={"version_id": vid},
        )
        assert r.status_code == 404


def test_upload_ring_to_version_with_lid_succeeds():
    """Symmetric to the above: LID first, then RING also succeeds."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid, vid = _new_version(client, "lid-then-ring-coexist")

        r1 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("lid.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "LID"},
        )
        assert r1.status_code == 200, r1.text
        lid_id = r1.json()["file_id"]

        r2 = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("ring.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "RING"},
        )
        assert r2.status_code == 200, r2.text
        ring_id = r2.json()["file_id"]

        v = _version_payload(client, pid, vid)
        assert [f["id"] for f in v["files_by_role_all"]["LID"]]  == [lid_id]
        assert [f["id"] for f in v["files_by_role_all"]["RING"]] == [ring_id]


def test_upload_lid_to_empty_version_succeeds():
    """A LID upload to a version holding neither RING nor LID should
    bind cleanly. Confirms LID is a first-class role, not just an
    enum value."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid, vid = _new_version(client, "lid-on-empty-ok")

        r = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("lid.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "LID"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["dxf_role"] == "LID"

        v = _version_payload(client, pid, vid)
        assert len(v["files_by_role_all"]["LID"]) == 1
        assert v["files_by_role_all"]["RING"] == []


# ---- Dev-mode rule-check JSON upload -------------------------------------

def _valid_upload_payload() -> dict:
    """Minimal envelope-valid RuleChecking JSON for upload tests."""
    return {
        "Rule1": {
            "pass": True,
            "text": "uploaded happy-path",
            "rules": [{
                "part": "BD",
                "file_id": "abc123de",
                "from": "S1",
                "to":   "A1",
                "text": "distance = 8.5 mm (> 5)",
                "tol":      None,
                "tol_text": None,
            }],
        },
    }


def test_upload_rule_json_writes_persisted_result():
    """Happy path: a valid envelope is persisted to
    `data/rule_check/{vid}.json` and immediately retrievable via the
    same GET the dashboard uses."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "upload-rc-happy")
        payload = _valid_upload_payload()

        r = client.post(f"/api/versions/{vid}/rule-check/upload", json=payload)
        assert r.status_code == 200, r.text
        summary = r.json()
        assert summary["rule_count"] == 1
        assert summary["pass_count"] == 1
        assert summary["fail_count"] == 0

        g = client.get(f"/api/versions/{vid}/rule-check")
        assert g.status_code == 200
        assert g.json()["results"] == payload


def test_upload_rule_json_coordinate_mode_round_trips():
    """A coordinate-mode envelope (point-to-point distance + to_entity
    outline, no file_id) validates, persists, and round-trips
    (add-rule-check-coordinate-display)."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "upload-rc-coords")
        payload = {
            "Cross-product gap": {
                "pass": False,
                "text": "ring-to-lid gap out of spec",
                "rules": [{
                    "part": "BD",
                    "text": "gap 0.42 mm",
                    "from_coordinates": [10.0, 20.0],
                    "to_coordinates": [13.0, 24.0],
                    "to_entity": [[0, 0], [5, 0], [5, 5], [0, 5]],
                }],
            },
        }
        r = client.post(f"/api/versions/{vid}/rule-check/upload", json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["rule_count"] == 1
        assert r.json()["pass_count"] == 0
        g = client.get(f"/api/versions/{vid}/rule-check")
        assert g.status_code == 200
        assert g.json()["results"] == payload


def test_upload_rule_json_rejects_invalid_envelope():
    """Envelope violation (`from` set, `file_id` null) returns 400 with
    the validator's error message so the user can fix the JSON
    inline."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "upload-rc-bad")
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
        r = client.post(f"/api/versions/{vid}/rule-check/upload", json=bad)
        assert r.status_code == 400, r.text
        assert "file_id" in r.json()["detail"]
        # Nothing got persisted — the subsequent GET still 404s.
        assert client.get(f"/api/versions/{vid}/rule-check").status_code == 404


def test_upload_rule_json_404_unknown_version():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post(
            "/api/versions/no-such-vid/rule-check/upload",
            json=_valid_upload_payload(),
        )
        assert r.status_code == 404


def test_upload_rule_json_overwrites_previous_result():
    """A second upload replaces the first — the persisted file holds
    the latest content, no merging."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "upload-rc-replace")

        first = _valid_upload_payload()
        r1 = client.post(f"/api/versions/{vid}/rule-check/upload", json=first)
        assert r1.status_code == 200

        second = {
            "Rule2": {
                "pass": False,
                "text": "different rule",
                "rules": [],
            },
        }
        r2 = client.post(f"/api/versions/{vid}/rule-check/upload", json=second)
        assert r2.status_code == 200

        g = client.get(f"/api/versions/{vid}/rule-check").json()
        assert g["results"] == second
        assert "Rule1" not in g["results"]


def test_upload_rule_json_rejects_malformed_body():
    """Non-JSON body is rejected with 400 before envelope validation runs."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        _, vid = _new_version(client, "upload-rc-malformed")
        r = client.post(
            f"/api/versions/{vid}/rule-check/upload",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
