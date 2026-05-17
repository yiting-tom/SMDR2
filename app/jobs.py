"""Background job queue (ProcessPoolExecutor + in-memory job dict).

Two job kinds, both running on the same worker pool:

- **discover**: cheap first pass — parse DXF, enumerate layers, render a
  per-layer SVG preview, drop the full primitive set into a transient file
  so Phase 2 can skip re-parsing. Outputs:
      data/layer_preview/{file_id}/layers.json
      data/layer_preview/{file_id}/<safe_name>.svg
      data/layer_preview/{file_id}/primitives.json   (transient)
- **preprocess**: existing heavy pipeline, now filtered to the user's
  chosen layer subset. Outputs (as before):
      data/parsed/{file_id}.json
      data/prematch/{file_id}.json
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

from app.storage import (
    layer_manifest_path,
    layer_preview_dir,
    layer_preview_primitives_path,
    layer_preview_svg_path,
    parsed_path,
    prematch_path,
    upload_path,
)


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
    selected_layers: list[str] | None = None,
    transient_primitives: str | None = None,
) -> dict[str, Any]:
    # Imports inside so spawned workers re-import cleanly.
    from app.dxf import filter_primitives, flatten_for_render
    from app.library import Store, build_handle_index
    from app.matching import build_entity_shapes, find_matches_from_pointsets
    from app.storage import DB_PATH

    # 1. Get primitives — reuse Phase 1's transient cache if present, else
    #    re-parse the DXF.
    bbox: tuple[float, float, float, float] | None
    background: str
    primitives: list[dict[str, Any]]
    insunits: int | None
    if transient_primitives and Path(transient_primitives).exists():
        with open(transient_primitives) as f:
            cached = json.load(f)
        primitives = cached["primitives"]
        bbox = tuple(cached["bbox"]) if cached.get("bbox") else None
        background = cached.get("background", "#ffffff")
        insunits = cached.get("insunits")
    else:
        out = flatten_for_render(src)
        primitives = out.primitives
        bbox = out.bbox
        background = out.background
        insunits = out.insunits

    # 2. Apply layer filter (None = legacy, keep everything).
    if selected_layers is not None:
        primitives = filter_primitives(primitives, selected_layers)

    Path(parsed_dst).parent.mkdir(parents=True, exist_ok=True)
    with open(parsed_dst, "w") as f:
        json.dump(
            {
                "primitives": primitives,
                "bbox": bbox,
                "background": background,
                "selected_layers": (
                    list(selected_layers) if selected_layers is not None else None
                ),
            },
            f,
        )

    # 3. Build shape index for matching
    handle_index = build_handle_index(primitives)
    shapes = build_entity_shapes(primitives, handle_index)

    # 4. Pre-match against this file's library (read fresh from SQLite).
    store = Store(DB_PATH)
    _, templates_by_class = store.load_library(library_id)
    by_class: dict[str, list[str]] = {}
    for cls_name, templates in templates_by_class.items():
        seen: set[str] = set()
        for tmpl in templates:
            result = find_matches_from_pointsets(
                tmpl.entity_point_sets, shapes,
                entity_kinds=tmpl.entity_kinds,
            )
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

    # 5. Discard the transient primitives cache — Phase 2 succeeded.
    if transient_primitives:
        try:
            Path(transient_primitives).unlink()
        except FileNotFoundError:
            pass

    return {
        "file_id": file_id,
        "primitive_count": len(primitives),
        "bbox": bbox,
        "background": background,
        "insunits": insunits,
        "prematch_total": sum(len(v) for v in by_class.values()),
    }


# ---- Job orchestration ----------------------------------------------------
def submit_preprocess(
    file_id: str,
    library_id: str = "default",
    selected_layers: list[str] | None = None,
) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "file_id": file_id,
            "library_id": library_id,
            "kind": "preprocess",
            "phase": "preprocess",
            "status": "queued",
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
    transient = layer_preview_primitives_path(file_id)
    fut = _get_executor().submit(
        _preprocess_worker,
        file_id,
        str(upload_path(file_id)),
        str(parsed_path(file_id)),
        str(prematch_path(file_id)),
        library_id,
        list(selected_layers) if selected_layers is not None else None,
        str(transient),
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
        insunits=result.get("insunits"),
    )


# ---- Discover-layers worker (Phase 1) ------------------------------------
def _discover_layers_worker(
    file_id: str,
    src: str,
    preview_dir: str,
) -> dict[str, Any]:
    """Parse a DXF once, render per-layer SVG thumbnails, and persist the
    full primitive set for Phase 2 reuse. Returns a manifest summary."""
    from app.dxf import (
        flatten_for_render,
        group_primitives_by_layer,
        render_layer_svg,
        sanitize_layer_name,
    )

    out = flatten_for_render(src)
    by_layer = group_primitives_by_layer(out.primitives)

    preview = Path(preview_dir)
    preview.mkdir(parents=True, exist_ok=True)

    # Per-layer SVG thumbnails.
    layers: list[dict[str, Any]] = []
    for name in sorted(by_layer.keys()):
        indices = by_layer[name]
        safe = sanitize_layer_name(name)
        svg = render_layer_svg(
            out.primitives, indices, out.bbox, background=out.background,
        )
        (preview / f"{safe}.svg").write_text(svg)
        layers.append({
            "name": name,
            "safe_name": safe,
            "svg_filename": f"{safe}.svg",
            "entity_count": len(indices),
        })

    manifest = {
        "file_id": file_id,
        "layers": layers,
        "bbox": list(out.bbox) if out.bbox else None,
        "background": out.background,
    }
    (preview / "layers.json").write_text(json.dumps(manifest))

    # Transient primitives cache — Phase 2 picks it up to skip re-parsing.
    (preview / "primitives.json").write_text(json.dumps({
        "primitives": out.primitives,
        "bbox": out.bbox,
        "background": out.background,
        "insunits": out.insunits,
    }))

    return {
        "file_id": file_id,
        "layer_count": len(layers),
        "bbox": out.bbox,
        "background": out.background,
    }


def submit_discover_layers(file_id: str) -> str:
    """Kick off Phase 1 in the worker pool. The file moves to
    `awaiting_layers` once the manifest is ready."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "file_id": file_id,
            "kind": "discover",
            "phase": "discover",
            "status": "queued",
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
    fut = _get_executor().submit(
        _discover_layers_worker,
        file_id,
        str(upload_path(file_id)),
        str(layer_preview_dir(file_id)),
    )
    fut.add_done_callback(lambda f: _on_discover_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


def _on_discover_done(job_id: str, fut: Future) -> None:
    from app.files import AWAITING_LAYERS, ERROR, FILE_STORE
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
        FILE_STORE.update_status(file_id, ERROR, error=f"{e}\n{tb}")
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    FILE_STORE.update_status(file_id, AWAITING_LAYERS)


# ---- Reads ----------------------------------------------------------------
def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def list_jobs() -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values()]
