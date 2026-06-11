"""AuthStore + identity dependency (Phase 0 — endpoint-independent).

Schema and semantics under test: docs/schema-auth-jobs.md. Grant rules the
DB can't express (admin global-only, dept viewer-only) must be raised by
the store; effective_role is the single resolution rule the Phase 3
endpoint dependencies will call.
"""

from __future__ import annotations

import pytest

from app.auth import (
    LOCK_TTL_SECONDS,
    SEED_CUSTOMER_ID,
    AuthStore,
    GrantError,
    Identity,
    get_identity,
)


CLAIMS = {
    "preferred_username": "editor1",
    "sub": "uuid-sub-1",
    "email": "editor1@example.test",
    "name": "Hua Lin",
    "deptid": "D100",
    "deptname": "封裝工程一部",
    "company": "EXAMPLE-CO",
    "twsitecode": "TW1",
    "supervisorid": "E00002",  # deliberately unstored
}


@pytest.fixture
def store(tmp_path) -> AuthStore:
    return AuthStore(tmp_path / "auth.sqlite")


def ident(userid: str, deptid: str = "") -> Identity:
    return Identity(userid=userid, deptid=deptid, source="test")


# ---- users -----------------------------------------------------------------
def test_first_login_creates_user_without_grants_and_audits(store):
    user, first = store.upsert_user_from_claims(CLAIMS)
    assert first is True
    assert user.userid == "editor1"
    assert user.deptid == "D100"
    assert store.list_grants("user", "editor1") == []
    actions = [a["action"] for a in store.list_audit()]
    assert actions == ["user.first_login"]


def test_relogin_refreshes_dept_and_audits_once(store):
    store.upsert_user_from_claims(CLAIMS)
    moved = dict(CLAIMS, deptid="D200", deptname="封裝工程二部")
    user, first = store.upsert_user_from_claims(moved)
    assert first is False
    assert user.deptid == "D200"
    actions = [a["action"] for a in store.list_audit()]
    assert actions.count("user.first_login") == 1


def test_missing_preferred_username_rejected(store):
    with pytest.raises(GrantError):
        store.upsert_user_from_claims({"sub": "x"})


def test_known_deptids_from_seen_logins(store):
    store.upsert_user_from_claims(CLAIMS)
    store.upsert_user_from_claims(
        dict(CLAIMS, preferred_username="editor2", deptid="D200")
    )
    assert store.known_deptids() == ["D100", "D200"]


# ---- grants ----------------------------------------------------------------
def test_admin_grant_must_be_global_user(store):
    with pytest.raises(GrantError):
        store.add_grant(grantee_type="user", grantee_id="a", role="admin",
                        scope_type="product", scope_id="p1", granted_by="x")
    with pytest.raises(GrantError):
        store.add_grant(grantee_type="dept", grantee_id="D100", role="admin",
                        scope_type="global", granted_by="x")


def test_dept_grant_viewer_only(store):
    with pytest.raises(GrantError):
        store.add_grant(grantee_type="dept", grantee_id="D100", role="editor",
                        scope_type="global", granted_by="x")
    g = store.add_grant(grantee_type="dept", grantee_id="D100", role="viewer",
                        scope_type="global", granted_by="x")
    assert g.role == "viewer"


def test_global_scope_id_sentinel_enforced(store):
    with pytest.raises(GrantError):
        store.add_grant(grantee_type="user", grantee_id="a", role="editor",
                        scope_type="global", scope_id="p1", granted_by="x")
    with pytest.raises(GrantError):
        store.add_grant(grantee_type="user", grantee_id="a", role="editor",
                        scope_type="product", scope_id="", granted_by="x")


def test_duplicate_grant_rejected(store):
    kw = dict(grantee_type="user", grantee_id="a", role="editor",
              scope_type="product", scope_id="p1", granted_by="x")
    store.add_grant(**kw)
    with pytest.raises(GrantError):
        store.add_grant(**kw)


def test_grant_and_revoke_are_audited(store):
    g = store.add_grant(grantee_type="user", grantee_id="a", role="viewer",
                        scope_type="global", granted_by="boss")
    assert store.revoke_grant(g.id, actor="boss") is True
    assert store.revoke_grant(g.id, actor="boss") is False
    actions = [a["action"] for a in store.list_audit()]
    assert actions == ["grant.revoke", "grant.create"]
    revoke = store.list_audit()[0]
    assert revoke["detail"]["grantee_id"] == "a"


def test_bootstrap_admins_idempotent(store):
    assert store.bootstrap_admins(["admin1", "admin2", ""]) == 2
    assert store.bootstrap_admins(["admin1", "admin2"]) == 0
    assert store.effective_role(ident("admin1")) == "admin"


# ---- effective_role ---------------------------------------------------------
def test_no_grants_means_no_role(store):
    assert store.effective_role(ident("nobody", "D9")) is None


def test_global_grant_applies_everywhere(store):
    store.add_grant(grantee_type="user", grantee_id="a", role="editor",
                    scope_type="global", granted_by="x")
    assert store.effective_role(ident("a")) == "editor"
    assert store.effective_role(
        ident("a"), product_id="p1", customer_id="c1"
    ) == "editor"


def test_product_and_customer_scopes_only_match_their_ids(store):
    store.add_grant(grantee_type="user", grantee_id="a", role="editor",
                    scope_type="product", scope_id="p1", granted_by="x")
    store.add_grant(grantee_type="user", grantee_id="a", role="viewer",
                    scope_type="customer", scope_id="c1", granted_by="x")
    assert store.effective_role(ident("a")) is None
    assert store.effective_role(ident("a"), product_id="p1") == "editor"
    assert store.effective_role(ident("a"), product_id="p2") is None
    assert store.effective_role(ident("a"), customer_id="c1") == "viewer"
    assert store.effective_role(
        ident("a"), product_id="p1", customer_id="c1"
    ) == "editor"  # highest wins


def test_dept_grant_follows_users_row_dept(store):
    store.add_grant(grantee_type="dept", grantee_id="D100", role="viewer",
                    scope_type="customer", scope_id="c1", granted_by="x")
    assert store.effective_role(
        ident("a", deptid="D100"), customer_id="c1"
    ) == "viewer"
    # after a transfer the same user no longer matches the dept grant
    assert store.effective_role(
        ident("a", deptid="D200"), customer_id="c1"
    ) is None


def test_bypass_identity_is_admin(store):
    assert store.effective_role(
        Identity(userid="dev", source="bypass")
    ) == "admin"


# ---- customers ---------------------------------------------------------------
def test_seed_customer_exists_and_is_protected(store):
    ids = [c["id"] for c in store.list_customers()]
    assert SEED_CUSTOMER_ID in ids
    with pytest.raises(GrantError):
        store.delete_customer(SEED_CUSTOMER_ID, actor="boss")


def test_customer_crud_audited(store):
    cid = store.create_customer("客戶A", actor="boss")
    assert store.get_customer(cid)["name"] == "客戶A"
    assert store.delete_customer(cid, actor="boss") is True
    actions = [a["action"] for a in store.list_audit()]
    assert actions == ["customer.delete", "customer.create"]


# ---- product edit locks ------------------------------------------------------
def test_lock_acquire_and_contention(store):
    assert store.acquire_lock("p1", "alice", now=1000.0).acquired
    st = store.acquire_lock("p1", "bob", now=1010.0)
    assert not st.acquired
    assert st.held_by == "alice"
    # re-acquiring one's own lock refreshes, never fails
    assert store.acquire_lock("p1", "alice", now=1020.0).acquired
    assert store.lock_holder("p1", now=1030.0) == "alice"


def test_zombie_lock_stolen_after_ttl(store):
    store.acquire_lock("p1", "alice", now=1000.0)
    t = 1000.0 + LOCK_TTL_SECONDS + 1
    assert store.lock_holder("p1", now=t) is None
    assert store.acquire_lock("p1", "bob", now=t).acquired
    assert store.lock_holder("p1", now=t) == "bob"


def test_heartbeat_keeps_lock_alive(store):
    store.acquire_lock("p1", "alice", now=1000.0)
    assert store.heartbeat_lock("p1", "alice", now=1200.0) is True
    assert store.lock_holder("p1", now=1000.0 + LOCK_TTL_SECONDS + 1) == "alice"
    assert store.heartbeat_lock("p1", "bob", now=1200.0) is False


def test_owner_release_and_admin_force_release(store):
    store.acquire_lock("p1", "alice", now=1000.0)
    assert store.release_lock("p1", "bob") is False        # not the owner
    assert store.release_lock("p1", "alice") is True
    store.acquire_lock("p1", "alice", now=2000.0)
    assert store.release_lock("p1", "the_admin", force=True) is True
    audit = store.list_audit()[0]
    assert audit["action"] == "lock.force_release"
    assert audit["detail"]["was_held_by"] == "alice"


# ---- identity dependency ------------------------------------------------------
def test_get_identity_bypass_default(monkeypatch):
    monkeypatch.delenv("SMDR2_AUTH_MODE", raising=False)
    monkeypatch.setenv("SMDR2_DEV_USER", "ops-dev")
    who = get_identity()
    assert who.userid == "ops-dev"
    assert who.is_bypass


def test_get_identity_oidc_mode_unauthenticated(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("SMDR2_AUTH_MODE", "oidc")
    with pytest.raises(HTTPException) as exc:
        get_identity()
    assert exc.value.status_code == 401
