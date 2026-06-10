"""Version lifecycle, clone semantics, sign-off freeze, and the
(version_id, file_id) artifact-keying invariants.

Spec: openspec/changes/add-product-versioning/specs/product-versioning/spec.md
"""

from __future__ import annotations

import json
import uuid


from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app)


def _new_product(client, name=None, label="v1"):
    r = client.post("/api/products", json={
        "name": name or f"vt-{uuid.uuid4().hex[:6]}",
        "version_label": label,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["versions"][0]["id"]


# ---- 6.1 lifecycle ---------------------------------------------------------

def test_create_product_requires_version_label():
    with _client() as c:
        assert c.post("/api/products", json={"name": "x"}).status_code == 422
        r = c.post("/api/products", json={"name": "x", "version_label": "  "})
        assert r.status_code == 422


def test_duplicate_label_within_product_409s_and_changes_nothing():
    with _client() as c:
        pid, _ = _new_product(c)
        r = c.post(f"/api/products/{pid}/versions", json={"label": "v1"})
        assert r.status_code == 409
        versions = c.get(f"/api/products/{pid}/versions").json()["versions"]
        assert [v["label"] for v in versions] == ["v1"]


def test_same_label_under_different_products_is_fine():
    with _client() as c:
        _new_product(c, label="v1")
        pid2, _ = _new_product(c, label="v1")
        assert c.get(f"/api/products/{pid2}/versions").json()["versions"][0]["label"] == "v1"


def test_no_version_delete_route():
    with _client() as c:
        pid, vid = _new_product(c)
        assert c.delete(f"/api/versions/{vid}").status_code in (404, 405)


def test_product_delete_cascades_versions_and_libraries():
    import sqlite3
    from app.storage import DB_PATH
    with _client() as c:
        pid, vid = _new_product(c)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()
        lib_ids = [vid, v2["id"]]
        r = c.delete(f"/api/products/{pid}")
        assert r.status_code == 200 and r.json()["versions_removed"] == 2
        con = sqlite3.connect(DB_PATH)
        assert con.execute(
            "SELECT COUNT(*) FROM versions WHERE product_id = ?", (pid,)
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM version_files WHERE version_id IN (?, ?)",
            tuple(lib_ids),
        ).fetchone()[0] == 0


# ---- 6.2 clone -------------------------------------------------------------

def _commit_template(version_id, class_name="SMD-2T", geom=None):
    from app.library import LIBRARIES, Template
    from app.versions import VERSION_STORE
    v = VERSION_STORE.get(version_id)
    lib = LIBRARIES.get(v.library_id)
    t = Template.from_entities(
        class_name, geom or [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]
    )
    return lib.add_template_for_file(t)


def test_clone_copies_templates_config_and_bindings():
    from app.files import FILE_STORE, READY
    from app.library import LIBRARIES
    from app.versions import VERSION_STORE
    with _client() as c:
        pid, vid = _new_product(c)
        _commit_template(vid)
        r = c.put(f"/api/versions/{vid}/classes/SMD-2T/strategy",
                  json={"strategy": "signature", "bbox_ratio": 0.4})
        assert r.status_code == 200
        FILE_STORE.register_content("cl0n3f11e00aa", "f.dxf", 1)
        FILE_STORE.bind(vid, "SBT", "cl0n3f11e00aa", initial_status=READY)
        FILE_STORE.update_selected_layers(vid, "cl0n3f11e00aa", ["L1"])

        r = c.post(f"/api/products/{pid}/versions", json={"label": "v2"})
        assert r.status_code == 200, r.text
        v2 = r.json()

        ver2 = VERSION_STORE.get(v2["id"])
        lib2 = LIBRARIES.get(ver2.library_id)
        assert lib2.count("SMD-2T") == 1
        assert lib2.strategy_of("SMD-2T") == ("signature", 0.4)
        b2 = FILE_STORE.get(v2["id"], "cl0n3f11e00aa")
        assert b2 is not None
        assert b2.dxf_role == "SBT"
        assert b2.selected_layers == ["L1"]
        # Cloned binding's artifacts don't exist yet → lifecycle reset.
        assert b2.status == "preprocessing"
        # Bytes are shared, not copied.
        assert FILE_STORE.binding_count("cl0n3f11e00aa") == 2


def test_clone_from_selects_an_older_version():
    from app.library import LIBRARIES
    from app.versions import VERSION_STORE
    with _client() as c:
        pid, v1 = _new_product(c)
        _commit_template(v1)  # v1 has 1 template
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()
        _commit_template(v2["id"], geom=[[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]])
        # v2 now has 2 templates; clone v3 from v1 → 1 template.
        v3 = c.post(f"/api/products/{pid}/versions",
                    json={"label": "v3", "clone_from": v1}).json()
        lib3 = LIBRARIES.get(VERSION_STORE.get(v3["id"]).library_id)
        assert lib3.count("SMD-2T") == 1


def test_clone_from_another_product_400s():
    with _client() as c:
        pid_a, _ = _new_product(c)
        _, vid_b = _new_product(c)
        r = c.post(f"/api/products/{pid_a}/versions",
                   json={"label": "v2", "clone_from": vid_b})
        assert r.status_code == 400


def test_editing_clone_does_not_touch_source():
    from app.library import LIBRARIES
    from app.versions import VERSION_STORE
    with _client() as c:
        pid, v1 = _new_product(c)
        _commit_template(v1)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()
        _commit_template(v2["id"], geom=[[(0.0, 0.0), (3.0, 0.0), (3.0, 3.0)]])
        lib1 = LIBRARIES.get(VERSION_STORE.get(v1).library_id)
        lib2 = LIBRARIES.get(VERSION_STORE.get(v2["id"]).library_id)
        assert lib1.count("SMD-2T") == 1
        assert lib2.count("SMD-2T") == 2


# ---- 6.3 sign-off freeze ----------------------------------------------------

def test_sign_off_records_identity_and_freezes_mutations(monkeypatch):
    monkeypatch.setenv("SMDR2_DEV_USER", "ignored-after-import")
    with _client() as c:
        pid, vid = _new_product(c)
        r = c.post(f"/api/versions/{vid}/sign-off")
        assert r.status_code == 200
        body = r.json()
        assert body["signed_off_by"]  # dev placeholder identity
        assert body["signed_off_at"] is not None

        # idempotence guard
        assert c.post(f"/api/versions/{vid}/sign-off").status_code == 409

        # representative mutations all 409
        blocked = [
            c.post(f"/api/versions/{vid}/files",
                   files={"file": ("a.dxf", b"x", "application/dxf")},
                   data={"dxf_role": "BD"}),
            c.put(f"/api/versions/{vid}/classes/SMD-2T/strategy",
                  json={"strategy": "chamfer"}),
            c.post(f"/api/versions/{vid}/rule-check"),
            c.post("/api/files/zzz/match-json", params={"version_id": vid}),
            c.post("/api/files/zzz/discover-layers", params={"version_id": vid}),
        ]
        for resp in blocked:
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["error"].startswith("version")

        # reads still work
        assert c.get(f"/api/products/{pid}/versions").status_code == 200
        assert c.get(f"/api/versions/{vid}/classes").status_code == 200

        # unsign reopens
        r = c.delete(f"/api/versions/{vid}/sign-off")
        assert r.status_code == 200 and r.json()["signed_off_by"] is None
        r = c.put(f"/api/versions/{vid}/classes/SMD-2T/strategy",
                  json={"strategy": "chamfer"})
        assert r.status_code == 200


def test_template_mutations_blocked_on_signed_version():
    with _client() as c:
        pid, vid = _new_product(c)
        tpl, _ = _commit_template(vid)
        assert c.post(f"/api/versions/{vid}/sign-off").status_code == 200
        assert c.delete(f"/api/templates/{tpl.id}").status_code == 409
        assert c.patch(f"/api/templates/{tpl.id}",
                       json={"class_name": "Pin-1"}).status_code == 409
        # unsign → delete goes through
        c.delete(f"/api/versions/{vid}/sign-off")
        assert c.delete(f"/api/templates/{tpl.id}").status_code == 200


# ---- 6.4 artifact keying ----------------------------------------------------

def test_artifacts_keyed_per_version_do_not_cross_contaminate():
    from app.storage import match_path, parsed_path, rule_check_path
    with _client() as c:
        pid, v1 = _new_product(c)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()

        fid = "aa11bb22cc33dd44"
        p1 = match_path(v1, fid)
        p2 = match_path(v2["id"], fid)
        assert p1 != p2
        p1.parent.mkdir(parents=True, exist_ok=True)
        p2.parent.mkdir(parents=True, exist_ok=True)
        p1.write_text(json.dumps({"v": 1}))
        p2.write_text(json.dumps({"v": 2}))
        assert json.loads(p1.read_text()) == {"v": 1}

        assert parsed_path(v1, fid) != parsed_path(v2["id"], fid)
        assert rule_check_path(v1) != rule_check_path(v2["id"])


def test_per_version_layer_selection_is_independent():
    from app.files import FILE_STORE, READY
    with _client() as c:
        pid, v1 = _new_product(c)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()
        fid = "ee55ff66aa77bb88"
        FILE_STORE.register_content(fid, "f.dxf", 1)
        FILE_STORE.bind(v1, "SBT", fid, initial_status=READY)
        FILE_STORE.bind(v2["id"], "SBT", fid, initial_status=READY)
        FILE_STORE.update_selected_layers(v1, fid, ["A"])
        FILE_STORE.update_selected_layers(v2["id"], fid, ["A", "B"])
        assert FILE_STORE.get(v1, fid).selected_layers == ["A"]
        assert FILE_STORE.get(v2["id"], fid).selected_layers == ["A", "B"]


def test_rule_check_result_of_old_version_stays_readable():
    with _client() as c:
        pid, v1 = _new_product(c)
        payload = {
            "RuleX": {"pass": True, "text": "t", "rules": []},
        }
        r = c.post(f"/api/versions/{v1}/rule-check/upload", json=payload)
        assert r.status_code == 200, r.text
        # sign off + create a successor — v1's result must stay readable
        assert c.post(f"/api/versions/{v1}/sign-off").status_code == 200
        c.post(f"/api/products/{pid}/versions", json={"label": "v2"})
        g = c.get(f"/api/versions/{v1}/rule-check")
        assert g.status_code == 200
        assert g.json()["results"] == payload
        assert g.json()["version_id"] == v1
