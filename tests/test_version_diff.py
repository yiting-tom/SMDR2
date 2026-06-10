"""Version diff (C6): template/config/binding comparison between two
snapshots of the same product.

Spec: openspec/changes/add-version-diff/specs/product-versioning/spec.md
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def _client():
    from app.main import app
    return TestClient(app)


def _new_product(client, label="v1"):
    r = client.post("/api/products", json={
        "name": f"vd-{uuid.uuid4().hex[:6]}",
        "version_label": label,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"], r.json()["versions"][0]["id"]


def _commit_template(version_id, class_name="SMD-2T", geom=None):
    from app.library import LIBRARIES, Template
    from app.versions import VERSION_STORE
    v = VERSION_STORE.get(version_id)
    lib = LIBRARIES.get(v.library_id)
    t = Template.from_entities(
        class_name, geom or [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]
    )
    return lib.add_template_for_file(t)


def _bind(version_id, role, fid, name=None):
    from app.files import FILE_STORE, READY
    FILE_STORE.register_content(fid, name or f"{fid}.dxf", 1)
    FILE_STORE.bind(version_id, role, fid, initial_status=READY)


def _diff(client, pid, v_from, v_to):
    r = client.get(f"/api/products/{pid}/version-diff",
                   params={"from": v_from, "to": v_to})
    assert r.status_code == 200, r.text
    return r.json()


def test_clone_with_no_edits_is_identical():
    with _client() as c:
        pid, v1 = _new_product(c)
        _commit_template(v1)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()["id"]
        d = _diff(c, pid, v1, v2)
        assert d["summary"]["identical"] is True
        assert d["templates"] == {"added": [], "removed": []}
        assert d["configs"] == []
        assert d["bindings"] == []


def test_template_added_in_newer_version():
    with _client() as c:
        pid, v1 = _new_product(c)
        _commit_template(v1)  # carried over by clone — must NOT appear
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()["id"]
        _commit_template(v2, geom=[[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)]])
        d = _diff(c, pid, v1, v2)
        assert d["summary"]["templates_added"] == 1
        assert d["summary"]["templates_removed"] == 0
        entry = d["templates"]["added"][0]
        assert entry["class_name"] == "SMD-2T"
        assert entry["entity_point_sets"]  # thumbnail-renderable

        # Reverse direction reports it as removed.
        d_rev = _diff(c, pid, v2, v1)
        assert d_rev["summary"]["templates_removed"] == 1


def test_config_change_reported_per_class():
    with _client() as c:
        pid, v1 = _new_product(c)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()["id"]
        r = c.put(f"/api/versions/{v2}/classes/SMD-2T/strategy",
                  json={"strategy": "signature", "bbox_ratio": 0.4})
        assert r.status_code == 200
        d = _diff(c, pid, v1, v2)
        assert d["summary"]["configs_changed"] == 1
        entry = d["configs"][0]
        assert entry["class_name"] == "SMD-2T"
        assert entry["from"]["match_strategy"] == "chamfer"
        assert entry["to"] == {"match_strategy": "signature", "bbox_ratio": 0.4}


def test_replaced_role_file_appears_as_binding_change():
    from app.files import FILE_STORE
    with _client() as c:
        pid, v1 = _new_product(c)
        _bind(v1, "SBT", "d1ff5b70000000aa", name="sbt.dxf")
        _bind(v1, "POD", "d1ff90d0000000bb", name="pod-old.dxf")
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()["id"]
        # carried bindings: replace only POD in v2
        FILE_STORE.unbind(v2, "d1ff90d0000000bb")
        _bind(v2, "POD", "d1ff90d0000000cc", name="pod-new.dxf")

        d = _diff(c, pid, v1, v2)
        kinds = {(b["role"], b["kind"]) for b in d["bindings"]}
        assert ("POD", "removed") in kinds
        assert ("POD", "added") in kinds
        assert not any(b["role"] == "SBT" for b in d["bindings"]), (
            "carried-over SBT must not appear in the diff"
        )


def test_state_change_on_shared_file_is_reported():
    from app.files import FILE_STORE
    with _client() as c:
        pid, v1 = _new_product(c)
        _bind(v1, "SBT", "d1ff5747e000aa11")
        FILE_STORE.update_selected_layers(v1, "d1ff5747e000aa11", ["A"])
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()["id"]
        FILE_STORE.update_selected_layers(v2, "d1ff5747e000aa11", ["A", "B"])

        d = _diff(c, pid, v1, v2)
        entry = next(b for b in d["bindings"] if b["kind"] == "state_changed")
        assert entry["role"] == "SBT"
        assert "selected_layers" in entry["changed"]


def test_cross_product_comparison_rejected():
    with _client() as c:
        pid_a, v_a = _new_product(c)
        _, v_b = _new_product(c)
        r = c.get(f"/api/products/{pid_a}/version-diff",
                  params={"from": v_a, "to": v_b})
        assert r.status_code == 400


def test_unknown_version_404s_and_missing_params_422():
    with _client() as c:
        pid, v1 = _new_product(c)
        r = c.get(f"/api/products/{pid}/version-diff",
                  params={"from": v1, "to": "nope"})
        assert r.status_code == 404
        assert c.get(f"/api/products/{pid}/version-diff").status_code == 422


def test_signed_off_versions_are_comparable():
    with _client() as c:
        pid, v1 = _new_product(c)
        v2 = c.post(f"/api/products/{pid}/versions", json={"label": "v2"}).json()["id"]
        assert c.post(f"/api/versions/{v1}/sign-off").status_code == 200
        assert c.post(f"/api/versions/{v2}/sign-off").status_code == 200
        d = _diff(c, pid, v1, v2)
        assert d["summary"]["identical"] is True
