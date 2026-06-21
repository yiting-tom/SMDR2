"""DB-backed job rows (docs/schema-auth-jobs.md §7, specs/job-queue).

Replaces the in-memory `_jobs` dict: submission INSERTs a `queued` row,
any web replica answers polls with a SELECT, and workers claim via the
two-step optimistic protocol (SELECT a candidate, then a conditional
UPDATE judged by rowcount — portable across SQLite and MariaDB, see
design D3 for why not FOR UPDATE SKIP LOCKED).

The dict shape returned by `to_dict()` preserves the old `_jobs` entry
keys (`id/kind/status/submitted_at/.../result/error/total/done/skipped/
errors`) so `GET /api/jobs/{id}` consumers see no change.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app import db
from app.dbschema import ensure_versioned_schema
from app.storage import DB_PATH

# Claimed-but-silent jobs are requeued after this long; workers heartbeat
# every HEARTBEAT_SECONDS while a job runs.
HEARTBEAT_SECONDS = 30.0
STALE_AFTER_SECONDS = 120.0
MAX_ATTEMPTS = 3
RETENTION_SECONDS = 7 * 24 * 3600.0

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    version_id   TEXT,
    file_id      TEXT,
    product_id   TEXT,
    parent_id    TEXT,
    payload      TEXT NOT NULL,
    result       TEXT,
    error        TEXT,
    total        INTEGER,
    done         INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    submitted_by TEXT,
    submitted_at REAL NOT NULL,
    started_at   REAL,
    completed_at REAL,
    claimed_by   TEXT,
    heartbeat_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_queue   ON jobs(status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_jobs_binding ON jobs(kind, version_id, file_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_parent  ON jobs(parent_id);
"""

def _row_to_dict(row: db.Row) -> dict[str, Any]:
    d = dict(row)
    payload = json.loads(d.pop("payload") or "{}")
    result = d.pop("result")
    d["result"] = json.loads(result) if result else None
    # Reprocess-all bookkeeping lives in payload; surface the legacy keys.
    for k in ("skipped", "errors"):
        if k in payload:
            d[k] = payload[k]
    d["_payload"] = payload
    return d


class JobStore:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_versioned_schema(path)
        self.conn = db.connect(path)
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(JOBS_SCHEMA)

    # ---- submission ---------------------------------------------------------
    def insert(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        version_id: str | None = None,
        file_id: str | None = None,
        product_id: str | None = None,
        parent_id: str | None = None,
        total: int | None = None,
        done: int | None = None,
        status: str = "queued",
        submitted_by: str | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO jobs (id, kind, status, version_id, file_id,"
                " product_id, parent_id, payload, total, done, attempts,"
                " submitted_by, submitted_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (job_id, kind, status, version_id, file_id, product_id,
                 parent_id, json.dumps(payload), total, done,
                 submitted_by, now),
            )
        return job_id

    # ---- reads ----------------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY submitted_at"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def find_inflight(
        self, kind: str, version_id: str, file_id: str,
    ) -> str | None:
        """Cross-replica dedupe — one queued/running job per binding."""
        with self.lock:
            row = self.conn.execute(
                "SELECT id FROM jobs WHERE kind = ? AND version_id = ?"
                " AND file_id = ? AND status IN ('queued','running')"
                " LIMIT 1",
                (kind, version_id, file_id),
            ).fetchone()
        return row["id"] if row else None

    def latest_for_version(self, kind: str, version_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE kind = ? AND version_id = ?"
                " ORDER BY submitted_at DESC LIMIT 1",
                (kind, version_id),
            ).fetchone()
        return _row_to_dict(row) if row else None

    # ---- claim protocol ----------------------------------------------------------
    def claim_next(self, worker: str, now: float | None = None) -> dict | None:
        """Two-step optimistic claim. Returns the claimed job dict or None
        when the queue is empty. Loops on claim races (rowcount=0)."""
        now = time.time() if now is None else now
        while True:
            with self.lock:
                row = self.conn.execute(
                    "SELECT id FROM jobs WHERE status = 'queued'"
                    " ORDER BY submitted_at LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            with self.lock, self.conn:
                cur = self.conn.execute(
                    "UPDATE jobs SET status='running', claimed_by=?,"
                    " started_at=?, heartbeat_at=?, attempts=attempts+1"
                    " WHERE id=? AND status='queued'",
                    (worker, now, now, row["id"]),
                )
            if cur.rowcount:
                return self.get(row["id"])
            # lost the race — pick again

    def heartbeat(self, worker: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE jobs SET heartbeat_at=? WHERE claimed_by=?"
                " AND status='running'",
                (now, worker),
            )
        return cur.rowcount

    # ---- completion -----------------------------------------------------------------
    def complete(self, job_id: str, result: dict | None) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET status='done', completed_at=?, result=?"
                " WHERE id=?",
                (time.time(),
                 json.dumps(result) if result is not None else None, job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET status='error', completed_at=?, error=?"
                " WHERE id=?",
                (time.time(), error, job_id),
            )

    def update_payload(self, job_id: str, payload: dict[str, Any]) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET payload=? WHERE id=?",
                (json.dumps(payload), job_id),
            )

    def bump_parent_done(self, parent_id: str) -> dict | None:
        """Atomic child-completion bookkeeping; returns the parent dict
        after the bump (caller decides whether it just finished)."""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE jobs SET done = COALESCE(done, 0) + 1 WHERE id=?",
                (parent_id,),
            )
        parent = self.get(parent_id)
        if (parent is not None and parent["status"] == "running"
                and parent["total"] is not None
                and (parent["done"] or 0) >= parent["total"]):
            with self.lock, self.conn:
                self.conn.execute(
                    "UPDATE jobs SET status='done', completed_at=?"
                    " WHERE id=? AND status='running'",
                    (time.time(), parent_id),
                )
            parent = self.get(parent_id)
        return parent

    def append_parent_error(self, parent_id: str, entry: dict) -> None:
        with self.lock, self.conn:
            row = self.conn.execute(
                "SELECT payload FROM jobs WHERE id=?", (parent_id,)
            ).fetchone()
            if row is None:
                return
            payload = json.loads(row["payload"] or "{}")
            payload.setdefault("errors", []).append(entry)
            self.conn.execute(
                "UPDATE jobs SET payload=? WHERE id=?",
                (json.dumps(payload), parent_id),
            )

    # ---- maintenance ---------------------------------------------------------------
    def requeue_stale(self, now: float | None = None) -> int:
        """Requeue running jobs whose heartbeat went silent and that have
        attempts left. Attempt-exhausted ones are NOT touched here —
        `stale_exhausted` hands them to the worker loop so the per-kind
        failure side effects (FILE_STORE error status, reprocess-all
        parent bookkeeping) run, same as any other failure."""
        now = time.time() if now is None else now
        cutoff = now - STALE_AFTER_SECONDS
        with self.lock, self.conn:
            return self.conn.execute(
                "UPDATE jobs SET status='queued', claimed_by=NULL,"
                " heartbeat_at=NULL WHERE status='running'"
                " AND heartbeat_at < ? AND attempts < ?",
                (cutoff, MAX_ATTEMPTS),
            ).rowcount

    def stale_exhausted(self, now: float | None = None) -> list[dict]:
        """Running jobs that are heartbeat-stale with no attempts left.
        The caller fails them through the normal failure path."""
        now = time.time() if now is None else now
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs WHERE status='running'"
                " AND heartbeat_at < ? AND attempts >= ?",
                (now - STALE_AFTER_SECONDS, MAX_ATTEMPTS),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def prune(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM jobs WHERE status IN ('done','error')"
                " AND completed_at < ?",
                (now - RETENTION_SECONDS,),
            )
        return cur.rowcount
