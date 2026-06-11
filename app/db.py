"""Engine-neutral DB connection layer (design D1, tasks 2.1/2.3).

Stores were written against the sqlite3 DBAPI (qmark params, sqlite3.Row,
`with conn:` transactions, executescript). This module keeps that exact
surface while routing through SQLAlchemy Core, so the per-store change is
just the connect call — and the same store code runs on MariaDB.

URL resolution (`resolve_url`):
- An explicit non-default path (tests' tmp dirs) → sqlite file URL. Test
  isolation never silently lands in a shared MariaDB.
- The default `DB_PATH` with `DATABASE_URL` set → `DATABASE_URL`
  (production MariaDB).
- Otherwise → sqlite file at DB_PATH (current behaviour).

Dialect handling lives HERE, not in stores:
- qmark → format placeholder translation for MySQL drivers (quote-aware).
- PRAGMAs applied only on sqlite.
- `executescript` (schema constants) runs only on sqlite — MariaDB schema
  is owned by Alembic migrations (task 2.2). Calling it on MySQL is a
  no-op by design.
- Driver IntegrityErrors are re-raised as `db.IntegrityError` so stores
  catch one exception type everywhere.

Single persistent connection per store, serialized by each store's RLock
(the sqlite model, kept deliberately — ≤10 concurrent users). A dropped
MariaDB connection (e.g. overnight wait_timeout) is transparently
reconnected and the statement retried once.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator, Sequence

import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy import exc as sa_exc


class IntegrityError(Exception):
    """Engine-neutral integrity violation (UNIQUE/PK/CHECK/FK)."""


def resolve_url(path: Path | str) -> str:
    """Map a store's path argument to a DB URL (see module docstring)."""
    from app.storage import DB_PATH
    env_url = os.environ.get("DATABASE_URL")
    if env_url and str(Path(path)) == str(DB_PATH):
        return env_url
    return f"sqlite:///{Path(path)}"


_engines: dict[str, sqlalchemy.Engine] = {}
_engines_lock = threading.Lock()


def _engine_for(url: str) -> sqlalchemy.Engine:
    with _engines_lock:
        eng = _engines.get(url)
        if eng is None:
            if url.startswith("sqlite"):
                eng = create_engine(
                    url,
                    poolclass=sqlalchemy.pool.NullPool,
                    connect_args={"check_same_thread": False},
                )
            else:
                eng = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
            _engines[url] = eng
        return eng


class Row:
    """Row supporting both access styles the stores use:
    `row["col"]`, `row[0]`, `dict(row)`, and `.keys()`."""

    __slots__ = ("_keys", "_values")

    def __init__(self, keys: Sequence[str], values: Sequence[Any]) -> None:
        self._keys = keys
        self._values = values

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._keys.index(key)]

    def keys(self) -> Sequence[str]:
        return self._keys

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Row):
            return (tuple(self._keys), tuple(self._values)) == (
                tuple(other._keys), tuple(other._values)
            )
        if isinstance(other, (tuple, list)):
            return tuple(self._values) == tuple(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._keys, self._values))!r})"


def _qmark_to_format(sql: str) -> str:
    """Translate `?` placeholders to `%s` for MySQL drivers, skipping
    quoted literals. Also escapes literal `%` so PyMySQL's printf-style
    interpolation never misfires on e.g. LIKE '%…%'."""
    out: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class Cursor:
    """The slice of the sqlite3 cursor surface the stores consume."""

    def __init__(self, rowcount: int, keys: Sequence[str], rows: list[tuple]):
        self.rowcount = rowcount
        self._keys = keys
        self._rows = rows
        self._idx = 0

    def fetchone(self) -> Row | None:
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return Row(self._keys, row)

    def fetchall(self) -> list[Row]:
        rows = [Row(self._keys, r) for r in self._rows[self._idx:]]
        self._idx = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Row]:
        while (row := self.fetchone()) is not None:
            yield row


class Connection:
    """sqlite3.Connection-shaped facade over a SQLAlchemy connection.

    Mirrors the semantics stores rely on:
    - `execute()` outside a `with conn:` block autocommits writes
      (sqlite3 legacy mode commits lazily; every store wraps writes it
      cares about in `with conn:`, so eager autocommit is equivalent).
    - `with conn:` opens a transaction, commits on exit, rolls back on
      exception. Reentrant `with` participates in the outer transaction
      (sqlite3 raises there, but our stores never nest).
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._engine = _engine_for(url)
        self._conn = self._engine.connect()
        self._tx: sqlalchemy.RootTransaction | None = None
        self._tx_depth = 0
        if self.is_sqlite:
            self._conn.exec_driver_sql("PRAGMA foreign_keys = ON")
            self._conn.exec_driver_sql("PRAGMA journal_mode = WAL")
            self._conn.commit()

    @property
    def is_sqlite(self) -> bool:
        return self._engine.dialect.name == "sqlite"

    # -- core ---------------------------------------------------------------
    def _translate(self, sql: str) -> str | None:
        """Dialect bridge for the few sqlite-isms the stores use. Returns
        None when the statement should be skipped entirely (PRAGMA on
        MySQL — connection tuning is the engine's job there)."""
        if self.is_sqlite:
            return sql
        if sql.lstrip().upper().startswith("PRAGMA"):
            return None
        sql = sql.replace("INSERT OR IGNORE", "INSERT IGNORE")
        sql = sql.replace("INSERT OR REPLACE", "REPLACE")
        sql = sql.replace("UPDATE OR IGNORE", "UPDATE IGNORE")
        return _qmark_to_format(sql)

    def _raw_execute(self, sql: str, params: Sequence[Any]) -> Cursor:
        translated = self._translate(sql)
        if translated is None:
            return Cursor(0, [], [])
        result = self._conn.exec_driver_sql(translated, tuple(params))
        if result.returns_rows:
            keys = list(result.keys())
            rows = [tuple(r) for r in result.fetchall()]
            return Cursor(result.rowcount, keys, rows)
        return Cursor(result.rowcount, [], [])

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        try:
            cur = self._raw_execute(sql, params)
        except sa_exc.IntegrityError as e:
            raise IntegrityError(str(e.orig)) from e
        except sqlite3.IntegrityError as e:  # sqlite raises pre-wrap on commit
            raise IntegrityError(str(e)) from e
        except sa_exc.DBAPIError as e:
            # One transparent reconnect+retry for dropped server links
            # (MariaDB wait_timeout) — only outside explicit transactions.
            if not (e.connection_invalidated and self._tx is None):
                raise
            self._conn = self._engine.connect()
            cur = self._raw_execute(sql, params)
        if self._tx is None:
            self._conn.commit()
        return cur

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any]]) -> Cursor:
        translated = self._translate(sql)
        params = [tuple(p) for p in seq_of_params]
        if translated is None or not params:
            # Empty batch: sqlite3.executemany no-ops, but SQLAlchemy would
            # execute the statement once with zero bindings — guard it.
            return Cursor(0, [], [])
        try:
            result = self._conn.exec_driver_sql(translated, params)
        except sa_exc.IntegrityError as e:
            raise IntegrityError(str(e.orig)) from e
        if self._tx is None:
            self._conn.commit()
        return Cursor(result.rowcount, [], [])

    def executescript(self, script: str) -> None:
        """Run a multi-statement schema script — sqlite only. On MariaDB
        the schema is owned by Alembic migrations; silently skipping keeps
        store __init__ identical across engines.

        Statements run one by one through `execute` so they join any open
        `with conn:` transaction (the native sqlite3 executescript issues
        its own COMMIT, which would kill the outer transaction). Splitting
        accumulates via sqlite3.complete_statement, which understands ';'
        inside comments and string literals (schema constants carry prose
        comments with semicolons)."""
        if not self.is_sqlite:
            return
        buf = ""
        for part in script.split(";"):
            buf += part + ";"
            if sqlite3.complete_statement(buf):
                stmt, buf = buf.strip(), ""
                if stmt.strip(";").strip():
                    self.execute(stmt)

    # -- transactions ---------------------------------------------------------
    def __enter__(self) -> "Connection":
        if self._tx is None:
            self._tx = self._conn.begin()
        self._tx_depth += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._tx_depth -= 1
        if self._tx_depth > 0:
            return
        tx, self._tx = self._tx, None
        try:
            if exc_type is None:
                tx.commit()
            else:
                tx.rollback()
        except sa_exc.IntegrityError as e:  # deferred sqlite UNIQUE on commit
            raise IntegrityError(str(e.orig)) from e

    def commit(self) -> None:
        if self._tx is not None:
            tx, self._tx = self._tx, None
            self._tx_depth = 0
            tx.commit()
        else:
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def connect(path: Path | str) -> Connection:
    """Store entry point — replaces `sqlite3.connect(...)`."""
    return Connection(resolve_url(path))
