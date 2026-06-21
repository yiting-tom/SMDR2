"""Admin API contracts: customers CRUD (RESTRICT + seed protection),
grants endpoint validation, audit filtering, /admin page gate."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import AUTH_STORE, Identity
from app.guards import current_identity
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _as_admin(suffix: str) -> Identity:
    uid = f"adm-{suffix}"
    try:
        AUTH_STORE.add_grant(grantee_type="user", grantee_id=uid, role="admin",
                             scope_type="global", granted_by="test")
    except Exception:
        pass
    ident = Identity(userid=uid, source="test")
    app.dependency_overrides[current_identity] = lambda: ident
    return ident


def _as_user(uid: str, deptid: str = ""):
    ident = Identity(userid=uid, deptid=deptid, source="test")
    app.dependency_overrides[current_identity] = lambda: ident
    return ident


def test_customer_crud_and_restrict(client):
    sfx = uuid.uuid4().hex[:6]
    _as_admin(sfx)
    r = client.post("/api/customers", json={"name": f"客戶X-{sfx}"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]

    # duplicate name → 409
    assert client.post("/api/customers", json={"name": f"客戶X-{sfx}"}).status_code == 409
    # seed protected
    assert client.delete("/api/customers/uncategorized").status_code == 400

    # customer with products → 409
    r = client.post("/api/products", json={
        "name": f"cp-{sfx}", "version_label": "v1", "customer_id": cid,
    })
    assert r.status_code == 200
    assert client.delete(f"/api/customers/{cid}").status_code == 409
    client.delete(f"/api/products/{r.json()['id']}")
    assert client.delete(f"/api/customers/{cid}").status_code == 200


def test_grants_endpoint_validation_and_revoke(client):
    sfx = uuid.uuid4().hex[:6]
    _as_admin(sfx)
    # unknown scope target → 400
    r = client.post("/api/grants", json={
        "grantee_type": "user", "grantee_id": f"u-{sfx}",
        "role": "viewer", "scope_type": "customer", "scope_id": "nope",
    })
    assert r.status_code == 400
    # dept editor → 400 (policy)
    r = client.post("/api/grants", json={
        "grantee_type": "dept", "grantee_id": f"D-{sfx}",
        "role": "editor", "scope_type": "global", "scope_id": "",
    })
    assert r.status_code == 400
    # valid dept viewer grant
    r = client.post("/api/grants", json={
        "grantee_type": "dept", "grantee_id": f"D-{sfx}",
        "role": "viewer", "scope_type": "global", "scope_id": "",
    })
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    listed = client.get("/api/grants").json()
    assert any(g["id"] == gid for g in listed["grants"])
    assert client.delete(f"/api/grants/{gid}").status_code == 200
    assert client.delete(f"/api/grants/{gid}").status_code == 404


def test_grants_admin_only(client):
    _as_user(f"pleb-{uuid.uuid4().hex[:6]}")
    assert client.get("/api/grants").status_code == 403
    assert client.post("/api/grants", json={
        "grantee_type": "user", "grantee_id": "x",
        "role": "viewer", "scope_type": "global", "scope_id": "",
    }).status_code == 403


def test_audit_filtering(client):
    sfx = uuid.uuid4().hex[:6]
    ident = _as_admin(sfx)
    AUTH_STORE.audit(actor=ident.userid, action=f"test.event-{sfx}",
                     target_type="t", target_id="x")
    data = client.get(f"/api/audit?actor={ident.userid}").json()["audit"]
    assert any(a["action"] == f"test.event-{sfx}" for a in data)
    data = client.get("/api/audit?action=no.such.action").json()["audit"]
    assert data == []


def test_admin_page_gate(client):
    _as_user(f"pleb-{uuid.uuid4().hex[:6]}")
    assert client.get("/admin").status_code == 403
    _as_admin(uuid.uuid4().hex[:6])
    assert client.get("/admin").status_code == 200
