"""Opt-in MariaDB smoke — exercises the db.py dialect bridges against a
real MariaDB (the compose one). Skipped unless SMDR2_MARIADB_SMOKE_URL is
set; Alembic must have been run against that DB first:

    DATABASE_URL=mysql+pymysql://conform:dev@127.0.0.1:3306/conform \
        uv run alembic upgrade head
    SMDR2_MARIADB_SMOKE_URL=mysql+pymysql://conform:dev@127.0.0.1:3306/conform \
        uv run pytest tests/test_mariadb_smoke.py -q

Covers exactly the translation seams that SQLite tests can't: INSERT
IGNORE, %s params with utf8mb4 content, unified IntegrityError, rowcount
semantics behind the lock protocol, and executemany batching.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

SMOKE_URL = os.environ.get("SMDR2_MARIADB_SMOKE_URL")

pytestmark = pytest.mark.skipif(
    not SMOKE_URL, reason="SMDR2_MARIADB_SMOKE_URL not set"
)


@pytest.fixture
def store(monkeypatch):
    from app.auth import AuthStore
    from app.storage import DB_PATH

    monkeypatch.setenv("DATABASE_URL", SMOKE_URL)
    s = AuthStore(DB_PATH)  # default path + DATABASE_URL → MariaDB
    assert not s.conn.is_sqlite, "smoke must run against MariaDB"
    yield s
    # Leave the shared dev DB the way we found it.
    with s.lock, s.conn:
        s.conn.execute("DELETE FROM role_grants WHERE granted_by = ?", ("smoke",))
        s.conn.execute("DELETE FROM users WHERE userid LIKE ?", ("smoke-%",))
        s.conn.execute(
            "DELETE FROM product_edit_locks WHERE product_id LIKE ?", ("smoke-%",)
        )
        s.conn.execute("DELETE FROM audit_log WHERE actor LIKE ?", ("smoke-%",))
    s.conn.close()


def test_seed_customer_via_insert_ignore(store):
    # AuthStore.__init__ already ran INSERT OR IGNORE → INSERT IGNORE twice
    # across fixture instantiations; exactly one seed row must exist.
    rows = store.conn.execute(
        "SELECT COUNT(*) FROM customers WHERE id = ?", ("uncategorized",)
    ).fetchone()
    assert rows[0] == 1


def test_user_roundtrip_with_utf8mb4(store):
    uid = f"smoke-{uuid.uuid4().hex[:8]}"
    user, first = store.upsert_user_from_claims({
        "preferred_username": uid,
        "deptid": "D100",
        "deptname": "封裝工程一部",
        "name": "煙霧測試",
        "email": f"{uid}@example.test",
    })
    assert first and user.deptname == "封裝工程一部"
    again, first2 = store.upsert_user_from_claims({
        "preferred_username": uid, "deptid": "D200", "deptname": "封裝工程二部",
    })
    assert not first2 and again.deptid == "D200"


def test_grant_unique_and_effective_role(store):
    from app.auth import GrantError, Identity
    uid = f"smoke-{uuid.uuid4().hex[:8]}"
    store.add_grant(grantee_type="user", grantee_id=uid, role="editor",
                    scope_type="product", scope_id="smoke-p1",
                    granted_by="smoke")
    with pytest.raises(GrantError):
        store.add_grant(grantee_type="user", grantee_id=uid, role="editor",
                        scope_type="product", scope_id="smoke-p1",
                        granted_by="smoke")
    ident = Identity(userid=uid, source="test")
    assert store.effective_role(ident, product_id="smoke-p1") == "editor"
    assert store.effective_role(ident, product_id="other") is None


def test_lock_protocol_rowcounts(store):
    pid = f"smoke-{uuid.uuid4().hex[:8]}"
    assert store.acquire_lock(pid, "smoke-alice", now=1000.0).acquired
    contested = store.acquire_lock(pid, "smoke-bob", now=1010.0)
    assert not contested.acquired and contested.held_by == "smoke-alice"
    t = 1000.0 + 301
    assert store.acquire_lock(pid, "smoke-bob", now=t).acquired


def test_executemany_batch(store):
    rows = [
        (f"smoke-{i}-{uuid.uuid4().hex[:6]}", "smoke batch", time.time())
        for i in range(3)
    ]
    with store.lock, store.conn:
        cur = store.conn.executemany(
            "INSERT INTO users (userid, name, created_at) VALUES (?,?,?)",
            rows,
        )
        assert cur.rowcount == 3
