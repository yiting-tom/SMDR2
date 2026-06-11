"""Identity + self-built authorization store (schema: docs/schema-auth-jobs.md).

Phase 0 scope — everything here is endpoint-independent:

- `AUTH_SCHEMA`: users / customers / role_grants / audit_log /
  product_edit_locks, created alongside the existing stores in the same
  SQLite file (MariaDB dialect mapping lives in the schema doc §8).
- `AuthStore`: login upsert, grants with the semantic rules the DB can't
  express, effective-role resolution, audit append, edit-lock protocol.
- `get_identity`: the FastAPI dependency endpoints will hang off in
  Phase 3. Until the BFF lands, `SMDR2_AUTH_MODE=bypass` (the default)
  yields a synthetic admin identity from `SMDR2_DEV_USER`, so wiring the
  dependency into endpoints changes nothing for dev or the test suite;
  tests inject real identities via `dependency_overrides` or by calling
  the store directly.

Semantic rules enforced HERE (deliberately not as CHECKs, so e.g. opening
dept grants to editor later is a one-line change):

- role='admin'      ⇒ scope_type='global' and grantee_type='user'
- grantee_type='dept' ⇒ role='viewer' (current policy)
- scope_type='global' ⇔ scope_id='' (sentinel — NULL would defeat the
  UNIQUE constraint; both engines treat NULLs as pairwise distinct)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app import db
from app.dbschema import ensure_versioned_schema
from app.storage import DB_PATH

ROLES = ("admin", "editor", "viewer")
SCOPE_TYPES = ("global", "customer", "product")
GRANTEE_TYPES = ("user", "dept")
_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}

# Edit-lock parameters (decided 2026-06-10): heartbeat every 30s, a lock
# whose heartbeat is older than TTL is a zombie and may be stolen.
LOCK_TTL_SECONDS = 300.0

# Session lifetimes (docs/schema-auth-jobs.md §4): idle 8h, absolute 24h.
# The absolute cap is also the answer to "how long can a deactivated
# account keep using an existing session".
SESSION_IDLE_SECONDS = 8 * 3600.0
SESSION_ABS_SECONDS = 24 * 3600.0
SESSION_COOKIE = "conform_session"

SEED_CUSTOMER_ID = "uncategorized"

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    userid        TEXT PRIMARY KEY,
    oidc_sub      TEXT,
    email         TEXT,
    name          TEXT,
    deptid        TEXT,
    deptname      TEXT,
    company       TEXT,
    twsitecode    TEXT,
    created_at    REAL NOT NULL,
    last_login_at REAL
);
CREATE INDEX IF NOT EXISTS idx_users_deptid ON users(deptid);

CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS role_grants (
    id           TEXT PRIMARY KEY,
    grantee_type TEXT NOT NULL CHECK (grantee_type IN ('user','dept')),
    grantee_id   TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin','editor','viewer')),
    scope_type   TEXT NOT NULL CHECK (scope_type IN ('global','customer','product')),
    scope_id     TEXT NOT NULL DEFAULT '',
    granted_by   TEXT NOT NULL,
    granted_at   REAL NOT NULL,
    UNIQUE (grantee_type, grantee_id, role, scope_type, scope_id),
    CHECK ((scope_type = 'global') = (scope_id = ''))
);
CREATE INDEX IF NOT EXISTS idx_grants_grantee ON role_grants(grantee_type, grantee_id);
CREATE INDEX IF NOT EXISTS idx_grants_scope ON role_grants(scope_type, scope_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    product_id  TEXT,
    version_id  TEXT,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at      ON audit_log(at);
CREATE INDEX IF NOT EXISTS idx_audit_target  ON audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_product ON audit_log(product_id);

CREATE TABLE IF NOT EXISTS product_edit_locks (
    product_id   TEXT PRIMARY KEY,
    held_by      TEXT NOT NULL,
    acquired_at  REAL NOT NULL,
    heartbeat_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    userid       TEXT NOT NULL,
    created_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    expires_at   REAL NOT NULL,
    csrf_token   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(userid);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


class GrantError(ValueError):
    """Semantic grant-rule violation → HTTP 400 at the API layer."""


@dataclass
class Identity:
    """Resolved caller identity. `deptid` is read from the users row
    (refreshed at login), NOT live from a JWT — dept grants follow the
    last-login department by design."""

    userid: str
    deptid: str = ""
    name: str = ""
    email: str = ""
    # 'session' | 'bypass' | 'test' — bypass short-circuits to admin.
    source: str = "session"

    @property
    def is_bypass(self) -> bool:
        return self.source == "bypass"


@dataclass
class User:
    userid: str
    oidc_sub: str | None
    email: str | None
    name: str | None
    deptid: str | None
    deptname: str | None
    company: str | None
    twsitecode: str | None
    created_at: float
    last_login_at: float | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Grant:
    id: str
    grantee_type: str
    grantee_id: str
    role: str
    scope_type: str
    scope_id: str
    granted_by: str
    granted_at: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class LockStatus:
    acquired: bool
    held_by: str
    heartbeat_at: float = field(default=0.0)


class AuthStore:
    """SQLite-backed auth store; shares the library.sqlite file and the
    store conventions (RLock + WAL, schema executescript on init)."""

    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_versioned_schema(path)
        self.conn = db.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(AUTH_SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO customers (id, name, created_at) "
                "VALUES (?, ?, ?)",
                (SEED_CUSTOMER_ID, "未分類", time.time()),
            )

    # ---- users -----------------------------------------------------------
    def upsert_user_from_claims(self, claims: dict) -> tuple[User, bool]:
        """Login landing point. Creates the row on first login (no grants),
        refreshes the JWT-derived columns on every login so dept grants
        follow transfers. Returns (user, first_login)."""
        userid = claims.get("preferred_username")
        if not userid:
            raise GrantError("JWT missing preferred_username")
        now = time.time()
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT userid FROM users WHERE userid = ?", (userid,)
            ).fetchone()
            first_login = row is None
            if first_login:
                self.conn.execute(
                    "INSERT INTO users (userid, oidc_sub, email, name, deptid,"
                    " deptname, company, twsitecode, created_at, last_login_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        userid,
                        claims.get("sub"),
                        claims.get("email"),
                        claims.get("name"),
                        claims.get("deptid"),
                        claims.get("deptname"),
                        claims.get("company"),
                        claims.get("twsitecode"),
                        now,
                        now,
                    ),
                )
                self._audit(
                    actor=userid, action="user.first_login",
                    target_type="user", target_id=userid,
                    detail={"deptid": claims.get("deptid")},
                )
            else:
                self.conn.execute(
                    "UPDATE users SET oidc_sub=?, email=?, name=?, deptid=?,"
                    " deptname=?, company=?, twsitecode=?, last_login_at=?"
                    " WHERE userid=?",
                    (
                        claims.get("sub"),
                        claims.get("email"),
                        claims.get("name"),
                        claims.get("deptid"),
                        claims.get("deptname"),
                        claims.get("company"),
                        claims.get("twsitecode"),
                        now,
                        userid,
                    ),
                )
        user = self.get_user(userid)
        assert user is not None
        return user, first_login

    def get_user(self, userid: str) -> User | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM users WHERE userid = ?", (userid,)
            ).fetchone()
        return User(**dict(row)) if row else None

    def known_deptids(self) -> list[str]:
        """Distinct deptids seen at login — the admin-UI dropdown source.
        Manually-typed unseen deptids are still legal grant targets."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT deptid FROM users"
                " WHERE deptid IS NOT NULL AND deptid != '' ORDER BY deptid"
            ).fetchall()
        return [r[0] for r in rows]

    # ---- customers -------------------------------------------------------
    def create_customer(self, name: str, actor: str) -> str:
        cid = str(uuid.uuid4())[:12]
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO customers (id, name, created_at) VALUES (?,?,?)",
                (cid, name, time.time()),
            )
            self._audit(
                actor=actor, action="customer.create",
                target_type="customer", target_id=cid, detail={"name": name},
            )
        return cid

    def get_customer(self, customer_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_customers(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM customers ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_customer(self, customer_id: str, actor: str) -> bool:
        """Plain delete + audit. The no-products RESTRICT rule is enforced
        at the API layer once products.customer_id lands (Phase 3)."""
        if customer_id == SEED_CUSTOMER_ID:
            raise GrantError("the seed customer cannot be deleted")
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM customers WHERE id = ?", (customer_id,)
            )
            if cur.rowcount:
                self._audit(
                    actor=actor, action="customer.delete",
                    target_type="customer", target_id=customer_id,
                )
        return cur.rowcount > 0

    # ---- grants ----------------------------------------------------------
    def add_grant(
        self,
        *,
        grantee_type: str,
        grantee_id: str,
        role: str,
        scope_type: str,
        scope_id: str = "",
        granted_by: str,
    ) -> Grant:
        if grantee_type not in GRANTEE_TYPES:
            raise GrantError(f"bad grantee_type {grantee_type!r}")
        if role not in ROLES:
            raise GrantError(f"bad role {role!r}")
        if scope_type not in SCOPE_TYPES:
            raise GrantError(f"bad scope_type {scope_type!r}")
        if (scope_type == "global") != (scope_id == ""):
            raise GrantError("scope_id must be '' for global and set otherwise")
        if role == "admin" and (scope_type != "global" or grantee_type != "user"):
            raise GrantError("admin grants are global + per-user only")
        if grantee_type == "dept" and role != "viewer":
            raise GrantError("dept grants are viewer-only (current policy)")
        gid = str(uuid.uuid4())[:12]
        now = time.time()
        try:
            with self.lock, self.conn:
                self.conn.execute(
                    "INSERT INTO role_grants (id, grantee_type, grantee_id,"
                    " role, scope_type, scope_id, granted_by, granted_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (gid, grantee_type, grantee_id, role, scope_type,
                     scope_id, granted_by, now),
                )
                self._audit(
                    actor=granted_by, action="grant.create",
                    target_type="grant", target_id=gid,
                    detail={
                        "grantee_type": grantee_type, "grantee_id": grantee_id,
                        "role": role, "scope_type": scope_type,
                        "scope_id": scope_id,
                    },
                )
        except db.IntegrityError as e:
            raise GrantError("duplicate grant") from e
        return Grant(gid, grantee_type, grantee_id, role, scope_type,
                     scope_id, granted_by, now)

    def revoke_grant(self, grant_id: str, actor: str) -> bool:
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT * FROM role_grants WHERE id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                return False
            self.conn.execute(
                "DELETE FROM role_grants WHERE id = ?", (grant_id,)
            )
            self._audit(
                actor=actor, action="grant.revoke",
                target_type="grant", target_id=grant_id,
                detail={k: row[k] for k in (
                    "grantee_type", "grantee_id", "role", "scope_type",
                    "scope_id",
                )},
            )
        return True

    def list_grants(
        self, grantee_type: str | None = None, grantee_id: str | None = None,
    ) -> list[Grant]:
        sql = "SELECT * FROM role_grants"
        args: tuple = ()
        if grantee_type is not None and grantee_id is not None:
            sql += " WHERE grantee_type = ? AND grantee_id = ?"
            args = (grantee_type, grantee_id)
        sql += " ORDER BY granted_at"
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [Grant(**dict(r)) for r in rows]

    def bootstrap_admins(self, userids: list[str]) -> int:
        """Idempotent BOOTSTRAP_ADMINS seeding (env, comma-separated).
        Returns how many admin grants were newly created."""
        created = 0
        for uid in userids:
            uid = uid.strip()
            if not uid:
                continue
            try:
                self.add_grant(
                    grantee_type="user", grantee_id=uid, role="admin",
                    scope_type="global", scope_id="", granted_by="system",
                )
                created += 1
            except GrantError:
                pass  # already an admin — idempotent
        return created

    # ---- resolution ------------------------------------------------------
    def effective_role(
        self,
        identity: Identity,
        *,
        product_id: str | None = None,
        customer_id: str | None = None,
    ) -> str | None:
        """Highest role the identity holds for the given scope context
        (admin > editor > viewer), or None. Global grants always apply;
        customer/product grants apply when the matching id is passed.
        Dept matching uses identity.deptid (the users-row value)."""
        if identity.is_bypass:
            return "admin"
        clauses = ["scope_type = 'global'"]
        args: list[str] = [identity.userid]
        dept_clause = "0"
        if identity.deptid:
            dept_clause = "(grantee_type = 'dept' AND grantee_id = ?)"
        if customer_id is not None:
            clauses.append("(scope_type = 'customer' AND scope_id = ?)")
        if product_id is not None:
            clauses.append("(scope_type = 'product' AND scope_id = ?)")
        sql = (
            "SELECT role FROM role_grants"
            " WHERE ((grantee_type = 'user' AND grantee_id = ?)"
            f"   OR {dept_clause})"
            f" AND ({' OR '.join(clauses)})"
        )
        if identity.deptid:
            args.append(identity.deptid)
        if customer_id is not None:
            args.append(customer_id)
        if product_id is not None:
            args.append(product_id)
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        if not rows:
            return None
        return max((r["role"] for r in rows), key=_ROLE_RANK.__getitem__)

    # ---- audit -----------------------------------------------------------
    def _audit(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        product_id: str | None = None,
        version_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        # Caller holds self.lock inside a transaction, or we open our own.
        self.conn.execute(
            "INSERT INTO audit_log (at, actor, action, product_id,"
            " version_id, target_type, target_id, detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), actor, action, product_id, version_id,
             target_type, target_id,
             json.dumps(detail) if detail is not None else None),
        )

    def audit(self, **kwargs) -> None:
        """Public audit append for actions owned by other modules
        (version.sign_off, lock.force_release, template.* …)."""
        with self.lock, self.conn:
            self._audit(**kwargs)

    def list_audit(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"]) if d["detail"] else None
            out.append(d)
        return out

    # ---- product edit locks (heartbeat 30s / TTL 300s) --------------------
    def acquire_lock(
        self, product_id: str, userid: str, now: float | None = None,
    ) -> LockStatus:
        """Explicit start-editing action. Re-acquiring one's own lock just
        refreshes the heartbeat. A zombie lock (heartbeat older than TTL)
        is stolen atomically — two replicas racing cannot both win."""
        now = time.time() if now is None else now
        # Each step is its own transaction: after an IntegrityError the
        # failed transaction must be rolled back before the connection is
        # reused (SQLAlchemy refuses further statements on it), so the
        # losing-INSERT → who-holds-it SELECT cannot share one `with`.
        with self.lock:
            with self.conn:
                cur = self.conn.execute(
                    "UPDATE product_edit_locks SET held_by=?, acquired_at=?,"
                    " heartbeat_at=? WHERE product_id=?"
                    " AND (held_by=? OR heartbeat_at < ?)",
                    (userid, now, now, product_id, userid,
                     now - LOCK_TTL_SECONDS),
                )
                if cur.rowcount:
                    return LockStatus(True, userid, now)
            try:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO product_edit_locks"
                        " (product_id, held_by, acquired_at, heartbeat_at)"
                        " VALUES (?,?,?,?)",
                        (product_id, userid, now, now),
                    )
                return LockStatus(True, userid, now)
            except db.IntegrityError:
                row = self.conn.execute(
                    "SELECT held_by, heartbeat_at FROM product_edit_locks"
                    " WHERE product_id=?", (product_id,),
                ).fetchone()
                return LockStatus(False, row["held_by"], row["heartbeat_at"])

    def heartbeat_lock(
        self, product_id: str, userid: str, now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE product_edit_locks SET heartbeat_at=?"
                " WHERE product_id=? AND held_by=?",
                (now, product_id, userid),
            )
        return cur.rowcount > 0

    def lock_holder(
        self, product_id: str, now: float | None = None,
    ) -> str | None:
        """Current live holder, or None (absent or expired)."""
        now = time.time() if now is None else now
        with self.lock:
            row = self.conn.execute(
                "SELECT held_by, heartbeat_at FROM product_edit_locks"
                " WHERE product_id=?", (product_id,),
            ).fetchone()
        if row is None or row["heartbeat_at"] < now - LOCK_TTL_SECONDS:
            return None
        return row["held_by"]

    def release_lock(
        self, product_id: str, actor: str, *, force: bool = False,
    ) -> bool:
        """Owner release, or admin force-release (audited)."""
        with self.lock, self.conn:
            if force:
                row = self.conn.execute(
                    "SELECT held_by FROM product_edit_locks WHERE product_id=?",
                    (product_id,),
                ).fetchone()
                cur = self.conn.execute(
                    "DELETE FROM product_edit_locks WHERE product_id=?",
                    (product_id,),
                )
                if cur.rowcount:
                    self._audit(
                        actor=actor, action="lock.force_release",
                        target_type="product", target_id=product_id,
                        product_id=product_id,
                        detail={"was_held_by": row["held_by"] if row else None},
                    )
            else:
                cur = self.conn.execute(
                    "DELETE FROM product_edit_locks"
                    " WHERE product_id=? AND held_by=?",
                    (product_id, actor),
                )
        return cur.rowcount > 0


    # ---- sessions (BFF server-side; docs/schema-auth-jobs.md §4) ----------
    def create_session(self, userid: str, now: float | None = None) -> tuple[str, str]:
        """Mint a session. Returns (cookie_token, csrf_token) — the DB
        stores only SHA-256(cookie_token), never the plaintext."""
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO sessions (id, userid, created_at, last_seen_at,"
                " expires_at, csrf_token) VALUES (?,?,?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), userid,
                 now, now, now + SESSION_ABS_SECONDS, csrf),
            )
        return token, csrf

    def resolve_session(
        self, token: str, now: float | None = None,
    ) -> dict | None:
        """Live session → {userid, deptid, name, email, csrf_token}, else
        None. Enforces idle (8h since last_seen) and absolute (24h)
        lifetimes; refreshes last_seen at most once a minute."""
        now = time.time() if now is None else now
        sid = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
        if row is None:
            return None
        if now > row["expires_at"] or now - row["last_seen_at"] > SESSION_IDLE_SECONDS:
            return None
        if now - row["last_seen_at"] > 60.0:
            with self.lock, self.conn:
                self.conn.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                    (now, sid),
                )
        user = self.get_user(row["userid"])
        return {
            "userid": row["userid"],
            "deptid": (user.deptid if user else "") or "",
            "name": (user.name if user else "") or "",
            "email": (user.email if user else "") or "",
            "csrf_token": row["csrf_token"],
        }

    def delete_session(self, token: str) -> bool:
        sid = hashlib.sha256(token.encode()).hexdigest()
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE id = ?", (sid,)
            )
        return cur.rowcount > 0

    def prune_sessions(self, now: float | None = None) -> int:
        """Drop sessions past either lifetime (worker-loop maintenance)."""
        now = time.time() if now is None else now
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?"
                " OR last_seen_at < ?",
                (now, now - SESSION_IDLE_SECONDS),
            )
        return cur.rowcount


# Module-level singleton (same pattern as FILE_STORE / PRODUCT_STORE).
AUTH_STORE = AuthStore()


# ---- FastAPI dependency ----------------------------------------------------
def get_identity(request=None) -> Identity:
    """Resolve the caller. Modes (SMDR2_AUTH_MODE):

    - 'bypass' (default until cutover): synthetic admin identity from
      SMDR2_DEV_USER — current single-user behaviour, keeps the suite
      green with the dependency wired into endpoints.
    - 'oidc': session-cookie lookup against AUTH_STORE; mutating methods
      additionally require the X-CSRF-Token header to match the session.

    Tests override this via `app.dependency_overrides[get_identity]`.
    """
    from fastapi import HTTPException
    mode = os.environ.get("SMDR2_AUTH_MODE", "bypass")
    if mode == "bypass":
        dev_user = os.environ.get("SMDR2_DEV_USER", "dev")
        return Identity(userid=dev_user, source="bypass")
    token = request.cookies.get(SESSION_COOKIE) if request is not None else None
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    sess = AUTH_STORE.resolve_session(token)
    if sess is None:
        raise HTTPException(status_code=401, detail="session expired")
    if request is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-CSRF-Token") != sess["csrf_token"]:
            raise HTTPException(status_code=403, detail="CSRF token mismatch")
    return Identity(
        userid=sess["userid"], deptid=sess["deptid"],
        name=sess["name"], email=sess["email"], source="session",
    )
