"""app/db.py facade — the sqlite3-shaped surface stores rely on, plus the
dialect bridges that make the same store code valid on MariaDB."""

from __future__ import annotations

import pytest

from app import db
from app.db import _qmark_to_format


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite")
    c.executescript(
        "CREATE TABLE t (id TEXT PRIMARY KEY, n INTEGER NOT NULL);"
    )
    yield c
    c.close()


# ---- URL resolution ---------------------------------------------------------
def test_explicit_path_is_always_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://x:y@h/db")
    url = db.resolve_url(tmp_path / "iso.sqlite")
    assert url.startswith("sqlite:///")


def test_default_path_follows_database_url(monkeypatch):
    from app.storage import DB_PATH
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://x:y@h/db")
    assert db.resolve_url(DB_PATH) == "mysql+pymysql://x:y@h/db"
    monkeypatch.delenv("DATABASE_URL")
    assert db.resolve_url(DB_PATH).startswith("sqlite:///")


# ---- DB URL from parts (Vault holds only username + password) ---------------
def _clear_db_env(monkeypatch):
    for k in ("DATABASE_URL", "DB_HOST", "DB_USER", "DB_PASSWORD",
              "DB_PORT", "DB_NAME", "DB_DRIVERNAME", "DB_CHARSET"):
        monkeypatch.delenv(k, raising=False)


def test_database_url_wins_over_parts(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://x:y@h/db")
    monkeypatch.setenv("DB_HOST", "ignored")
    monkeypatch.setenv("DB_USER", "ignored")
    assert db.resolve_database_url() == "mysql+pymysql://x:y@h/db"


def test_compose_from_parts(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "mariadb.prod.svc")
    monkeypatch.setenv("DB_USER", "conform")
    monkeypatch.setenv("DB_PASSWORD", "s3cret")
    monkeypatch.setenv("DB_PORT", "3306")
    monkeypatch.setenv("DB_NAME", "conform")
    url = db.resolve_database_url()
    assert url == (
        "mysql+pymysql://conform:s3cret@mariadb.prod.svc:3306/"
        "conform?charset=utf8mb4"
    )


def test_compose_defaults(monkeypatch):
    # Only the mandatory host+user → driver/db/charset defaults, no port.
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "h")
    monkeypatch.setenv("DB_USER", "u")
    url = db.resolve_database_url()
    assert url == "mysql+pymysql://u@h/conform?charset=utf8mb4"


def test_compose_password_special_chars_round_trip(monkeypatch):
    from sqlalchemy.engine import make_url
    _clear_db_env(monkeypatch)
    pw = "p@ss:w/o#rd?x"
    monkeypatch.setenv("DB_HOST", "h")
    monkeypatch.setenv("DB_USER", "u")
    monkeypatch.setenv("DB_PASSWORD", pw)
    url = db.resolve_database_url()
    # The raw password must NOT appear unescaped, but must parse back exactly.
    assert pw not in url
    assert make_url(url).password == pw


def test_no_db_env_is_none_and_sqlite(monkeypatch):
    from app.storage import DB_PATH
    _clear_db_env(monkeypatch)
    assert db.resolve_database_url() is None
    assert db.resolve_url(DB_PATH).startswith("sqlite:///")


def test_partial_parts_fall_back_to_sqlite(monkeypatch):
    # DB_HOST without DB_USER must NOT compose (avoid a half-built URL).
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "h")
    assert db.resolve_database_url() is None


def test_non_default_path_stays_sqlite_with_parts(tmp_path, monkeypatch):
    # Even with full DB_* parts, a non-default store path stays SQLite.
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "h")
    monkeypatch.setenv("DB_USER", "u")
    assert db.resolve_url(tmp_path / "iso.sqlite").startswith("sqlite:///")


# ---- qmark translation --------------------------------------------------------
def test_qmark_translation_skips_quoted_literals():
    # '?' inside literals survives; '%' is escaped even inside literals
    # (PyMySQL interpolates the whole statement, quotes included).
    sql = "SELECT * FROM t WHERE a = ? AND b = 'lit?eral' AND c LIKE '%x?%'"
    out = _qmark_to_format(sql)
    assert out == (
        "SELECT * FROM t WHERE a = %s AND b = 'lit?eral' AND c LIKE '%%x?%%'"
    )


def test_qmark_translation_escapes_bare_percent():
    assert _qmark_to_format("SELECT 1 % 2") == "SELECT 1 %% 2"


# ---- Row -----------------------------------------------------------------------
def test_row_supports_both_access_styles(conn):
    conn.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
    row = conn.execute("SELECT id, n FROM t").fetchone()
    assert row["id"] == "a" and row[1] == 1
    assert dict(row) == {"id": "a", "n": 1}
    assert list(row.keys()) == ["id", "n"]


# ---- transactions ----------------------------------------------------------------
def test_with_block_commits_and_rolls_back(conn):
    with conn:
        conn.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("b", 2))
            raise RuntimeError("abort")
    ids = [r["id"] for r in conn.execute("SELECT id FROM t").fetchall()]
    assert ids == ["a"]


def test_autocommit_outside_with_block(tmp_path):
    c1 = db.connect(tmp_path / "x.sqlite")
    c1.executescript("CREATE TABLE t (id TEXT PRIMARY KEY);")
    c1.execute("INSERT INTO t (id) VALUES (?)", ("a",))
    c2 = db.connect(tmp_path / "x.sqlite")
    assert c2.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    c1.close()
    c2.close()


def test_integrity_error_unified(conn):
    conn.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 1))
    with pytest.raises(db.IntegrityError):
        with conn:
            conn.execute("INSERT INTO t (id, n) VALUES (?, ?)", ("a", 2))


def test_executemany_and_rowcount(conn):
    cur = conn.executemany(
        "INSERT INTO t (id, n) VALUES (?, ?)", [("a", 1), ("b", 2)]
    )
    assert cur.rowcount == 2
    cur = conn.execute("DELETE FROM t WHERE n > ?", (0,))
    assert cur.rowcount == 2


def test_insert_or_ignore_works_on_sqlite(conn):
    conn.execute("INSERT OR IGNORE INTO t (id, n) VALUES (?, ?)", ("a", 1))
    conn.execute("INSERT OR IGNORE INTO t (id, n) VALUES (?, ?)", ("a", 9))
    assert conn.execute("SELECT n FROM t WHERE id = ?", ("a",)).fetchone()["n"] == 1


def test_mysql_dialect_translation():
    """Translation rules exercised without a live MySQL server."""
    class Fake(db.Connection):
        def __init__(self):  # bypass real connect
            pass
        is_sqlite = False
    f = Fake()
    assert f._translate("PRAGMA journal_mode = WAL") is None
    assert f._translate(
        "INSERT OR IGNORE INTO c (id) VALUES (?)"
    ) == "INSERT IGNORE INTO c (id) VALUES (%s)"
    assert f._translate(
        "INSERT OR REPLACE INTO c (id) VALUES (?)"
    ) == "REPLACE INTO c (id) VALUES (%s)"
    assert f._translate(
        "UPDATE OR IGNORE classes SET name = ? WHERE name = ?"
    ) == "UPDATE IGNORE classes SET name = %s WHERE name = %s"
