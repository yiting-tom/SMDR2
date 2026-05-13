"""Background job queue (ProcessPoolExecutor + in-memory job dict).

Each job runs the full pre-processing pipeline for one DXF in a subprocess:
parse → flatten primitives → build entity index → pre-match against the
current library snapshot. Outputs:
    data/parsed/{file_id}.json     — drawing primitives + bbox + background
    data/prematch/{file_id}.json   — handles by class, for instant overlay
"""

from __future__ import annotations

import json
import time
import traceback
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from threading import RLock
from typing import Any

from app.storage import parsed_path, prematch_path, upload_path


MAX_WORKERS = 2

_executor: ProcessPoolExecutor | None = None
_jobs: dict[str, dict[str, Any]] = {}
_lock = RLock()


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=MAX_WORKERS)
    return _executor


def shutdown() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


# ---- Pre-process worker (must be picklable for ProcessPool) --------------
def _preprocess_worker(
    file_id: str,
    src: str,
    parsed_dst: str,
    prematch_dst: str,
    library_id: str,
) -> dict[str, Any]:
    # Imports inside so spawned workers re-import cleanly.
    from app.dxf import flatten_for_render
    from app.library import Store, build_handle_index
    from app.matching import build_entity_shapes, find_matches_from_pointsets
    from app.storage import DB_PATH

    # 1. Parse + flatten
    out = flatten_for_render(src)
    Path(parsed_dst).parent.mkdir(parents=True, exist_ok=True)
    with open(parsed_dst, "w") as f:
        json.dump(
            {
                "primitives": out.primitives,
                "bbox": out.bbox,
                "background": out.background,
            },
            f,
        )

    # 2. Build shape index for matching
    handle_index = build_handle_index(out.primitives)
    shapes = build_entity_shapes(out.primitives, handle_index)

    # 3. Pre-match against this file's library (read fresh from SQLite).
    store = Store(DB_PATH)
    _, templates_by_class = store.load_library(library_id)
    by_class: dict[str, list[str]] = {}
    for cls_name, templates in templates_by_class.items():
        seen: set[str] = set()
        for tmpl in templates:
            result = find_matches_from_pointsets(tmpl.entity_point_sets, shapes)
            for m in result.matches:
                for h in m.handles:
                    seen.add(h)
        if seen:
            by_class[cls_name] = sorted(seen)

    Path(prematch_dst).parent.mkdir(parents=True, exist_ok=True)
    with open(prematch_dst, "w") as f:
        json.dump(
            {"by_class": by_class, "total": sum(len(v) for v in by_class.values())},
            f,
        )

    return {
        "file_id": file_id,
        "primitive_count": len(out.primitives),
        "bbox": out.bbox,
        "background": out.background,
        "prematch_total": sum(len(v) for v in by_class.values()),
    }


# ---- Job orchestration ----------------------------------------------------
def submit_preprocess(file_id: str, library_id: str = "default") -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "file_id": file_id,
            "library_id": library_id,
            "kind": "preprocess",
            "status": "queued",
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
    fut = _get_executor().submit(
        _preprocess_worker,
        file_id,
        str(upload_path(file_id)),
        str(parsed_path(file_id)),
        str(prematch_path(file_id)),
        library_id,
    )
    fut.add_done_callback(lambda f: _on_preprocess_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


# Backwards-compat alias — older code may still call submit_parse.
submit_parse = submit_preprocess


def _on_preprocess_done(job_id: str, fut: Future) -> None:
    from app.files import FILE_STORE  # local import to break cycle
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    file_id = job["file_id"]
    try:
        result = fut.result()
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = str(e)
            job["completed_at"] = time.time()
        FILE_STORE.update_status(file_id, "error", error=f"{e}\n{tb}")
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    FILE_STORE.update_parsed(
        file_id,
        primitive_count=result["primitive_count"],
        bbox=tuple(result["bbox"]) if result["bbox"] else (0, 0, 0, 0),
        background=result["background"],
    )


# ---- Reads ----------------------------------------------------------------
def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def list_jobs() -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values()]
