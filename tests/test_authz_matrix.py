"""Authorization matrix contracts (specs/authorization §endpoint access
matrix + specs/product-edit-lock): real identities injected through
`dependency_overrides[current_identity]`, grants written to the shared
AUTH_STORE, guards exercised over the live routes."""

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


def _as(userid: str, deptid: str = "") -> Identity:
    ident = Identity(userid=userid, deptid=deptid, source="test")
    app.dependency_overrides[current_identity] = lambda: ident
    return ident


def _grant(**kw):
    kw.setdefault("granted_by", "matrix-test")
    return AUTH_STORE.add_grant(**kw)


def _mk_product(client, name: str, customer_id: str = "uncategorized") -> dict:
    _as(f"adm-{uuid.uuid4().hex[:6]}-boot")
    _grant(grantee_type="user",
           grantee_id=app.dependency_overrides[current_identity]().userid,
           role="admin", scope_type="global")
    r = client.post("/api/products", json={
        "name": name, "version_label": "v1", "customer_id": customer_id,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---- visibility -------------------------------------------------------------
def test_grantless_user_sees_empty_system(client):
    _mk_product(client, f"vis-{uuid.uuid4().hex[:6]}")
    _as(f"nobody-{uuid.uuid4().hex[:6]}")
    r = client.get("/api/products")
    assert r.status_code == 200
    assert r.json()["products"] == []


def test_customer_scope_viewer_sees_only_their_customer(client):
    suffix = uuid.uuid4().hex[:6]
    cust = AUTH_STORE.create_customer(f"客戶A-{suffix}", actor="matrix-test")
    p_in = _mk_product(client, f"in-{suffix}", customer_id=cust)
    _mk_product(client, f"out-{suffix}")  # uncategorized

    uid = f"v-{suffix}"
    _grant(grantee_type="user", grantee_id=uid, role="viewer",
           scope_type="customer", scope_id=cust)
    _as(uid)
    names = [p["name"] for p in client.get("/api/products").json()["products"]]
    assert f"in-{suffix}" in names
    assert f"out-{suffix}" not in names
    # direct read of the in-scope product passes; detail of others 403s
    assert client.get(f"/api/products/{p_in['id']}").status_code == 200


def test_dept_viewer_grant_follows_deptid(client):
    suffix = uuid.uuid4().hex[:6]
    cust = AUTH_STORE.create_customer(f"客戶D-{suffix}", actor="matrix-test")
    p = _mk_product(client, f"dept-{suffix}", customer_id=cust)
    _grant(grantee_type="dept", grantee_id=f"D-{suffix}", role="viewer",
           scope_type="customer", scope_id=cust)

    _as(f"member-{suffix}", deptid=f"D-{suffix}")
    assert client.get(f"/api/products/{p['id']}").status_code == 200
    _as(f"outsider-{suffix}", deptid="D-other")
    assert client.get(f"/api/products/{p['id']}").status_code == 403


# ---- role floors --------------------------------------------------------------
def test_viewer_cannot_write(client):
    suffix = uuid.uuid4().hex[:6]
    p = _mk_product(client, f"ro-{suffix}")
    uid = f"viewer-{suffix}"
    _grant(grantee_type="user", grantee_id=uid, role="viewer",
           scope_type="product", scope_id=p["id"])
    _as(uid)
    r = client.post(f"/api/products/{p['id']}/versions", json={"label": "v2"})
    assert r.status_code == 403


def test_editor_requires_lock_then_succeeds(client):
    suffix = uuid.uuid4().hex[:6]
    p = _mk_product(client, f"lk-{suffix}")
    uid = f"editor-{suffix}"
    _grant(grantee_type="user", grantee_id=uid, role="editor",
           scope_type="product", scope_id=p["id"])
    _as(uid)

    r = client.post(f"/api/products/{p['id']}/versions", json={"label": "v2"})
    assert r.status_code == 423, r.text  # role ok, no lock

    assert client.post(f"/api/products/{p['id']}/lock").status_code == 200
    r = client.post(f"/api/products/{p['id']}/versions", json={"label": "v2"})
    assert r.status_code == 200, r.text


def test_editor_scope_does_not_leak_to_other_products(client):
    suffix = uuid.uuid4().hex[:6]
    p1 = _mk_product(client, f"own-{suffix}")
    p2 = _mk_product(client, f"other-{suffix}")
    uid = f"editor-{suffix}"
    _grant(grantee_type="user", grantee_id=uid, role="editor",
           scope_type="product", scope_id=p1["id"])
    _as(uid)
    assert client.post(f"/api/products/{p2['id']}/lock").status_code == 403


def test_product_creation_is_admin_only(client):
    uid = f"editor-{uuid.uuid4().hex[:6]}"
    _grant(grantee_type="user", grantee_id=uid, role="editor",
           scope_type="global")
    _as(uid)
    r = client.post("/api/products", json={"name": "x", "version_label": "v1"})
    assert r.status_code == 403


def test_unknown_customer_rejected_on_create(client):
    uid = f"adm-{uuid.uuid4().hex[:6]}"
    _grant(grantee_type="user", grantee_id=uid, role="admin",
           scope_type="global")
    _as(uid)
    r = client.post("/api/products", json={
        "name": "x", "version_label": "v1", "customer_id": "no-such",
    })
    assert r.status_code == 400


# ---- sign-off ---------------------------------------------------------------
def test_editor_signs_own_version_and_unsign_is_admin_only(client):
    suffix = uuid.uuid4().hex[:6]
    p = _mk_product(client, f"so-{suffix}")
    vid = p["versions"][0]["id"]
    uid = f"editor-{suffix}"
    _grant(grantee_type="user", grantee_id=uid, role="editor",
           scope_type="product", scope_id=p["id"])
    _as(uid)
    client.post(f"/api/products/{p['id']}/lock")

    r = client.post(f"/api/versions/{vid}/sign-off")
    assert r.status_code == 200, r.text
    assert r.json()["signed_off_by"] == uid
    actions = [a["action"] for a in AUTH_STORE.list_audit(20)]
    assert "version.sign_off" in actions

    # editor cannot unsign…
    assert client.delete(f"/api/versions/{vid}/sign-off").status_code == 403
    # …admin can
    adm = f"adm2-{suffix}"
    _grant(grantee_type="user", grantee_id=adm, role="admin",
           scope_type="global")
    _as(adm)
    assert client.delete(f"/api/versions/{vid}/sign-off").status_code == 200


# ---- effective_role in product payload (add-role-based-ui-gating) -----------
def test_product_payload_carries_effective_role(client):
    """`/api/products` (+ `/{id}`) expose the caller's per-product role so
    the UI can gate affordances. Value == guards.effective_role."""
    suffix = uuid.uuid4().hex[:6]
    p = _mk_product(client, f"er-{suffix}")

    # product-scoped viewer → role 'viewer' on that product
    viewer = f"v-{suffix}"
    _grant(grantee_type="user", grantee_id=viewer, role="viewer",
           scope_type="product", scope_id=p["id"])
    _as(viewer)
    row = next(x for x in client.get("/api/products").json()["products"]
               if x["id"] == p["id"])
    assert row["effective_role"] == "viewer"
    assert client.get(f"/api/products/{p['id']}").json()["effective_role"] == "viewer"

    # global editor → 'editor'
    editor = f"e-{suffix}"
    _grant(grantee_type="user", grantee_id=editor, role="editor",
           scope_type="global")
    _as(editor)
    row = next(x for x in client.get("/api/products").json()["products"]
               if x["id"] == p["id"])
    assert row["effective_role"] == "editor"

    # global admin → 'admin'
    adm = f"a-{suffix}"
    _grant(grantee_type="user", grantee_id=adm, role="admin",
           scope_type="global")
    _as(adm)
    row = next(x for x in client.get("/api/products").json()["products"]
               if x["id"] == p["id"])
    assert row["effective_role"] == "admin"


# ---- lock contention -----------------------------------------------------------
def test_lock_contention_and_admin_force_release(client):
    suffix = uuid.uuid4().hex[:6]
    p = _mk_product(client, f"lc-{suffix}")
    for u in (f"alice-{suffix}", f"bob-{suffix}"):
        _grant(grantee_type="user", grantee_id=u, role="editor",
               scope_type="product", scope_id=p["id"])

    _as(f"alice-{suffix}")
    assert client.post(f"/api/products/{p['id']}/lock").status_code == 200

    _as(f"bob-{suffix}")
    r = client.post(f"/api/products/{p['id']}/lock")
    assert r.status_code == 423
    assert r.json()["held_by"] == f"alice-{suffix}"

    adm = f"adm3-{suffix}"
    _grant(grantee_type="user", grantee_id=adm, role="admin",
           scope_type="global")
    _as(adm)
    assert client.delete(
        f"/api/products/{p['id']}/lock", params={"force": 1}
    ).status_code == 200
    assert any(a["action"] == "lock.force_release"
               for a in AUTH_STORE.list_audit(10))

    _as(f"bob-{suffix}")
    assert client.post(f"/api/products/{p['id']}/lock").status_code == 200
