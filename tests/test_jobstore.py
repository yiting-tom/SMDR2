"""JobStore unit contracts (specs/job-queue): claim protocol, stale
recovery split (requeue vs exhausted), parent bookkeeping, prune."""

from __future__ import annotations

import pytest

from app.jobstore import (
    MAX_ATTEMPTS,
    RETENTION_SECONDS,
    STALE_AFTER_SECONDS,
    JobStore,
)


@pytest.fixture
def store(tmp_path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite")


def test_claim_is_exactly_once(store):
    a = store.insert(kind="discover", payload={}, version_id="v", file_id="f")
    j1 = store.claim_next("w1", now=100.0)
    assert j1["id"] == a and j1["status"] == "running" and j1["attempts"] == 1
    assert store.claim_next("w2", now=101.0) is None  # nothing left


def test_requeue_stale_only_requeues_with_attempts_left(store):
    a = store.insert(kind="discover", payload={})
    store.claim_next("w1", now=100.0)
    # silent past the staleness window → requeued
    n = store.requeue_stale(now=100.0 + STALE_AFTER_SECONDS + 1)
    assert n == 1
    assert store.get(a)["status"] == "queued"
    # burn through the attempts
    for i in range(MAX_ATTEMPTS - 1):
        store.claim_next("w1", now=200.0 + i)
        store.requeue_stale(now=200.0 + i + STALE_AFTER_SECONDS + 1)
    job = store.get(a)
    assert job["attempts"] == MAX_ATTEMPTS
    # exhausted: requeue_stale must NOT touch it; stale_exhausted returns it
    t = 1000.0 + STALE_AFTER_SECONDS + 1
    store.claim_next("w1", now=1000.0)  # nothing queued → None; job already running?
    assert store.requeue_stale(now=t) == 0
    exhausted = store.stale_exhausted(now=t)
    assert [j["id"] for j in exhausted] == [a]
    # caller (worker loop) fails it through the normal path
    store.fail(a, "worker died")
    assert store.get(a)["status"] == "error"


def test_parent_bump_and_completion(store):
    parent = store.insert(kind="reprocess-all", payload={"skipped": 0, "errors": []},
                          total=2, done=0, status="running")
    store.bump_parent_done(parent)
    assert store.get(parent)["status"] == "running"
    p = store.bump_parent_done(parent)
    assert p["status"] == "done" and p["done"] == 2
    assert p["completed_at"] is not None


def test_append_parent_error_surfaces_in_dict(store):
    parent = store.insert(kind="reprocess-all", payload={"skipped": 1, "errors": []},
                          total=1, done=0, status="running")
    store.append_parent_error(parent, {"file_id": "f", "error": "boom"})
    d = store.get(parent)
    assert d["errors"] == [{"file_id": "f", "error": "boom"}]
    assert d["skipped"] == 1


def test_prune_only_removes_old_terminal_rows(store):
    import time
    done = store.insert(kind="discover", payload={})
    store.claim_next("w", now=100.0)
    store.complete(done, {"ok": 1})   # completed_at = real wall clock
    queued = store.insert(kind="discover", payload={})
    # too recent → kept
    assert store.prune(now=time.time()) == 0
    # past retention → done row pruned, queued row untouched
    assert store.prune(now=time.time() + RETENTION_SECONDS + 1) == 1
    assert store.get(done) is None
    assert store.get(queued) is not None


def test_find_inflight_sees_queued_and_running(store):
    a = store.insert(kind="preprocess", payload={}, version_id="v", file_id="f")
    assert store.find_inflight("preprocess", "v", "f") == a
    store.claim_next("w", now=1.0)
    assert store.find_inflight("preprocess", "v", "f") == a
    store.complete(a, {})
    assert store.find_inflight("preprocess", "v", "f") is None
