"""Job worker loop (specs/job-queue, design D3/D4).

Claims queued jobs from the DB with the two-step optimistic protocol and
executes them on a local ProcessPoolExecutor. Runs in two modes:

- standalone: ``python -m app.worker_loop`` — the k8s worker Deployment
  (webs set SMDR2_EMBEDDED_WORKER=0 and only enqueue).
- embedded: a daemon thread inside the web process, started lazily on
  first job submission (default ON) — keeps single-container dev and the
  test suite working with zero configuration.

Loop duties per tick: reap finished futures (apply each kind's store
side effects, then mark done/error), claim while executor slots remain,
heartbeat all running claims every 30s, requeue stale jobs and prune old
rows once a minute.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor

logger = logging.getLogger(__name__)


class WorkerLoop:
    def __init__(self, max_workers: int | None = None) -> None:
        from app import jobs
        self._jobs_mod = jobs
        # Bind the store at construction: a loop never starts claiming
        # from a store swapped in later (test isolation).
        self.store = jobs.JOB_STORE
        self.identity = f"{socket.gethostname()}:{os.getpid()}"
        self.max_workers = max_workers or jobs.MAX_WORKERS
        self._executor: ProcessPoolExecutor | None = None
        self._inflight: dict[str, tuple[dict, Future]] = {}
        self._stop = threading.Event()
        self._last_heartbeat = 0.0
        self._last_maintenance = 0.0

    # -- plumbing -------------------------------------------------------------
    def _exec(self) -> ProcessPoolExecutor:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
        return self._executor

    def stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        self.stop()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    # -- one tick ----------------------------------------------------------------
    def run_once(self) -> bool:
        """Reap + claim + maintain. Returns True when any work happened
        (callers use it to decide whether to sleep)."""
        jobs = self._jobs_mod
        store = self.store
        activity = False

        # 1. reap finished futures
        for job_id in [j for j, (_, f) in self._inflight.items() if f.done()]:
            job, fut = self._inflight.pop(job_id)
            activity = True
            try:
                result = fut.result()
            except Exception as exc:  # worker raised (or pool broke)
                tb = traceback.format_exc()
                jobs.apply_failure(job, exc, tb)
                store.fail(job_id, f"{exc}\n{tb}")
                logger.warning(
                    "job_failed id=%s kind=%s %s: %s",
                    job_id, job["kind"], type(exc).__name__, exc,
                )
            else:
                callback_error = jobs.apply_success(job, result)
                if callback_error:
                    store.fail(job_id, callback_error)
                else:
                    store.complete(job_id, result)
                logger.info("job_done id=%s kind=%s", job_id, job["kind"])

        # 2. claim while slots free
        while len(self._inflight) < self.max_workers and not self._stop.is_set():
            job = store.claim_next(self.identity)
            if job is None:
                break
            activity = True
            try:
                fn, args = jobs.execution_plan(job)
            except Exception as exc:
                tb = traceback.format_exc()
                jobs.apply_failure(job, exc, tb)
                store.fail(job["id"], f"{exc}\n{tb}")
                continue
            self._inflight[job["id"]] = (job, self._exec().submit(fn, *args))

        # 3. heartbeat + maintenance
        now = time.time()
        if self._inflight and now - self._last_heartbeat >= 25.0:
            store.heartbeat(self.identity, now)
            self._last_heartbeat = now
        if now - self._last_maintenance >= 60.0:
            requeued, failed = store.requeue_stale(now)
            if requeued or failed:
                logger.warning(
                    "stale jobs: requeued=%d failed=%d", requeued, failed
                )
            store.prune(now)
            self._last_maintenance = now
        return activity

    def run_forever(self, poll_interval: float = 0.1) -> None:
        logger.info(
            "worker loop up identity=%s max_workers=%d",
            self.identity, self.max_workers,
        )
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(poll_interval)
            except Exception:
                logger.exception("worker loop tick failed; continuing")
                self._stop.wait(1.0)


# ---- embedded mode ----------------------------------------------------------
_embedded: WorkerLoop | None = None
_embedded_lock = threading.Lock()


def ensure_embedded() -> None:
    """Start the in-process worker thread once (dev / tests / single
    container). No-op when SMDR2_EMBEDDED_WORKER=0 (k8s webs)."""
    global _embedded
    if os.environ.get("SMDR2_EMBEDDED_WORKER", "1") == "0":
        return
    with _embedded_lock:
        if _embedded is not None:
            return
        _embedded = WorkerLoop()
        t = threading.Thread(
            target=_embedded.run_forever, name="conform-worker", daemon=True
        )
        t.start()


def shutdown_embedded() -> None:
    global _embedded
    with _embedded_lock:
        if _embedded is not None:
            _embedded.shutdown()
            _embedded = None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    WorkerLoop().run_forever()


if __name__ == "__main__":
    main()
