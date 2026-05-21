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
    DATA_DIR,
    layer_manifest_path,
    layer_preview_dir,
    layer_preview_primitives_path,
    layer_preview_svg_path,
    parsed_path,
    prematch_path,
    rule_check_path,
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
    dev_overrides_snapshot: dict[str, Any] | None = None,
    user_unit_override: str | None = None,
) -> dict[str, Any]:
    # Imports inside so spawned workers re-import cleanly.
    import math
    from app.dxf import (
        detect_scale_factor,
        filter_primitives,
        flatten_for_render,
    )
    from app.library import Store, build_handle_index
    from app.matching import build_entity_shapes, find_matches_from_pointsets
    from app.storage import DB_PATH

    # Dev-mode tunable overrides made in the parent process don't reach
    # this worker (separate Python interpreter); the parent passes its
    # active snapshot here and we re-apply before any matching / parsing
    # touches the now-default module attributes.
    if dev_overrides_snapshot:
        from app.dev_overrides import apply_snapshot
        apply_snapshot(dev_overrides_snapshot)

    # 1. Get primitives — reuse Phase 1's transient cache if present, else
    #    re-parse the DXF. The transient cache from Phase 1 has no
    #    knowledge of a unit override, so when one is set we re-parse
    #    instead of trusting the cache (cheap — only the override path
    #    hits this, and only on operator action).
    bbox: tuple[float, float, float, float] | None
    background: str
    primitives: list[dict[str, Any]]
    insunits: int | None
    applied_scale: float
    use_cache = (
        user_unit_override is None
        and transient_primitives
        and Path(transient_primitives).exists()
    )
    if use_cache:
        with open(transient_primitives) as f:
            cached = json.load(f)
        primitives = cached["primitives"]
        bbox = tuple(cached["bbox"]) if cached.get("bbox") else None
        background = cached.get("background", "#ffffff")
        insunits = cached.get("insunits")
        applied_scale = float(cached.get("applied_scale", 1.0))
    else:
        out = flatten_for_render(src, user_unit_override=user_unit_override)
        primitives = out.primitives
        bbox = out.bbox
        background = out.background
        insunits = out.insunits
        applied_scale = out.applied_scale

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
    # Per-class (match_strategy, bbox_ratio) governs which matcher pipeline
    # runs for each class's templates.
    store = Store(DB_PATH)
    _, configs_by_class, templates_by_class = store.load_library(library_id)
    by_class: dict[str, list[str]] = {}
    for cls_name, templates in templates_by_class.items():
        cfg = configs_by_class.get(cls_name) or {}
        strategy = cfg.get("match_strategy") or "chamfer"
        bbox_ratio = cfg.get("bbox_ratio")
        seen: set[str] = set()
        for tmpl in templates:
            result = find_matches_from_pointsets(
                tmpl.entity_point_sets, shapes,
                entity_kinds=tmpl.entity_kinds,
                strategy=strategy,
                bbox_ratio=bbox_ratio,
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

    # `detector_factor` is what the auto-rescale detector *would* have
    # chosen for this file's `(insunits, pre-rescale diagonal)`. The
    # done-callback compares it against `applied_scale` to decide
    # whether to clear a redundant override (operator picked the same
    # unit the detector would have). When no override is active and
    # the worker reused the transient Phase 1 cache, we have no
    # original-bbox copy to derive it from — `None` skips the
    # comparison without affecting normal preprocess.
    detector_factor: float | None = None
    if not use_cache and bbox is not None and applied_scale > 0:
        dx = float(bbox[2]) - float(bbox[0])
        dy = float(bbox[3]) - float(bbox[1])
        post_diag = math.hypot(max(dx, 0.0), max(dy, 0.0))
        pre_diag = post_diag / applied_scale
        detector_factor = detect_scale_factor(insunits, pre_diag)

    return {
        "file_id": file_id,
        "primitive_count": len(primitives),
        "bbox": bbox,
        "background": background,
        "insunits": insunits,
        "applied_scale": applied_scale,
        "detector_factor": detector_factor,
        "user_unit_override_requested": user_unit_override,
        "prematch_total": sum(len(v) for v in by_class.values()),
    }


# ---- Job orchestration ----------------------------------------------------
def _current_dev_overrides() -> dict[str, Any]:
    """Snapshot of active dev overrides for handoff to a worker process.

    Lazy import keeps the override module out of the bootstrap path for
    deployments that never touch it.
    """
    from app.dev_overrides import snapshot_non_default
    return snapshot_non_default()


def submit_preprocess(
    file_id: str,
    library_id: str = "default",
    selected_layers: list[str] | None = None,
    user_unit_override: str | None = None,
) -> str:
    """Submit a preprocess job. When `user_unit_override` is None,
    the worker reads the file row's persisted override (set in a
    prior viewer-picker action). Callers that came in via the
    `/unit-override` endpoint pass the new unit explicitly so the
    job picks it up even before the row write commits."""
    # Worker runs in a separate process and can't trivially read the
    # row, so resolve the active override here and pass it through.
    if user_unit_override is None:
        from app.files import FILE_STORE
        rec = FILE_STORE.get(file_id)
        if rec is not None:
            user_unit_override = rec.user_unit_override
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
            "user_unit_override_requested": user_unit_override,
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
        _current_dev_overrides() or None,
        user_unit_override,
    )
    fut.add_done_callback(lambda f: _on_preprocess_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


def find_inflight_preprocess_job(file_id: str) -> str | None:
    """Return the id of any queued / running preprocess job for the
    given file, or None. Used by the unit-override endpoint to return
    `409 Conflict` instead of double-enqueueing."""
    with _lock:
        for job in _jobs.values():
            if (
                job.get("kind") == "preprocess"
                and job.get("file_id") == file_id
                and job.get("status") in ("queued", "running")
            ):
                return job["id"]
    return None


def submit_unit_override_preprocess(file_id: str, unit: str) -> str:
    """Operator-driven recompute triggered by the viewer's unit picker.
    Writes the override to the file row, flips status to PREPROCESSING,
    and enqueues the standard preprocess pipeline carrying the new
    unit so the worker uses it directly. The done-callback may clear
    the override back to NULL if it agrees with the detector — see
    `_on_preprocess_done`."""
    from app.files import FILE_STORE, PREPROCESSING

    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise KeyError(file_id)
    FILE_STORE.set_user_unit_override(file_id, unit)
    FILE_STORE.update_status(file_id, PREPROCESSING)
    return submit_preprocess(
        file_id,
        library_id=rec.library_id,
        selected_layers=rec.selected_layers,
        user_unit_override=unit,
    )


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
    factor_changed = FILE_STORE.update_parsed(
        file_id,
        primitive_count=result["primitive_count"],
        bbox=tuple(result["bbox"]) if result["bbox"] else (0, 0, 0, 0),
        background=result["background"],
        insunits=result.get("insunits"),
        applied_scale=float(result.get("applied_scale", 1.0)),
    )
    if factor_changed:
        _invalidate_match_after_rescale(file_id)
    _maybe_clear_redundant_unit_override(file_id, result)


def _invalidate_match_after_rescale(file_id: str) -> None:
    """Drop the saved per-file Match JSON when `applied_scale` changes.
    Saved point sets reference the prior coordinate system; rerunning is
    cheaper and safer than scaling the JSON in place. See the
    `dxf-pipeline` spec / `auto-normalize-unit-suspect-dxf` change."""
    from app.files import FILE_STORE
    from app.storage import match_path

    mp = match_path(file_id)
    try:
        mp.unlink()
    except FileNotFoundError:
        pass
    FILE_STORE.set_match_saved(file_id, False)


def _maybe_clear_redundant_unit_override(file_id: str, result: dict[str, Any]) -> None:
    """If the operator's override matches what the detector would have
    chosen anyway, persist `user_unit_override = NULL` so future
    detector improvements continue to apply automatically.

    Only runs when an override was actively in use for this job (the
    worker echoes back the requested unit). Skips when `detector_factor`
    is None (worker reused the Phase 1 transient cache — no pre-rescale
    diagonal available)."""
    from app.files import FILE_STORE

    requested = result.get("user_unit_override_requested")
    if not requested:
        return
    detector_factor = result.get("detector_factor")
    applied_scale = float(result.get("applied_scale", 1.0))
    if detector_factor is None:
        return
    if abs(float(detector_factor) - applied_scale) < 1e-12:
        FILE_STORE.set_user_unit_override(file_id, None)


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
        "applied_scale": out.applied_scale,
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


# ---- Rule-check worker ---------------------------------------------------
def _rule_check_worker(
    product_id: str,
    file_ids: list[str],
    dst: str,
) -> dict[str, Any]:
    """Run product-scoped DRC in a worker process.

    Materialises the DRC handoff bundle to a temp directory (same
    layout `app/drc_bundle.py:build_bundle` ships inside the zip),
    hands the directory path to `app.rule_check.check_rules`, and
    persists the returned RuleChecking JSON to ``dst``. The temp
    directory is removed when the call returns (success or failure)
    via the `materialise_bundle` context manager.
    """
    from app.drc_bundle import materialise_bundle
    from app.files import FILE_STORE
    from app.products import PRODUCT_STORE
    from app.rule_check import check_rules

    product = PRODUCT_STORE.get(product_id)
    if product is None:
        raise RuntimeError(f"product {product_id!r} not found in worker")
    files = []
    roles_seen: set[str] = set()
    for fid in file_ids:
        rec = FILE_STORE.get(fid)
        if rec is None:
            raise RuntimeError(f"file {fid!r} not found in worker")
        files.append(rec)
        if rec.dxf_role:
            roles_seen.add(rec.dxf_role)

    with materialise_bundle(product, files) as bundle_dir:
        result = check_rules(product_id, bundle_dir)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "w") as fp:
        json.dump(result, fp, indent=2)

    n_pass = sum(1 for v in result.values() if v.get("pass"))
    try:
        saved_to = str(dst_path.relative_to(DATA_DIR.parent))
    except ValueError:
        saved_to = str(dst_path)
    return {
        "product_id": product_id,
        "saved_to": saved_to,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
        "roles_covered": sorted(roles_seen),
    }


def submit_rule_check(
    product_id: str,
    file_ids: list[str],
) -> str:
    """Submit a product-scoped rule check to the worker pool. Returns
    the job_id immediately; the request handler should return 202 +
    {job_id} so the front-end can poll `GET /api/jobs/{job_id}`.

    The worker receives only the product id and the list of
    role-attached file ids; it re-opens the per-process `PRODUCT_STORE`
    / `FILE_STORE` to fetch records and materialise the bundle from
    on-disk DXF + Match JSON files."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "product_id": product_id,
            "kind": "rule_check",
            "status": "queued",
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
    fut = _get_executor().submit(
        _rule_check_worker,
        product_id,
        list(file_ids),
        str(rule_check_path(product_id)),
    )
    fut.add_done_callback(lambda f: _on_rule_check_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


def _on_rule_check_done(job_id: str, fut: Future) -> None:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    try:
        result = fut.result()
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = f"{e}\n{tb}"
            job["completed_at"] = time.time()
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result


# ---- Re-process-all (dev mode) -------------------------------------------
# Spec: POST /api/dev/reprocess-all returns ONE job_id whose progress
# (`total` / `done`) covers every file in storage. We fan out one
# `_preprocess_worker` future per file, share a parent job dict for
# reporting, and bump `done` from each child's completion callback.
# Match JSONs are not touched — only parsed/{file_id}.json and
# prematch/{file_id}.json get rewritten.
_REPROCESS_SKIP_STATUSES = frozenset({
    "discovering_layers",
    "awaiting_layers",
    "error",
})


def submit_reprocess_all(
    file_id_filter: set[str] | None = None,
    *,
    kind: str = "reprocess-all",
) -> str:
    """Re-preprocess every eligible file in storage with current overrides.

    Returns one parent job_id; `_jobs[job_id]` exposes
    `total`, `done`, `skipped`, `errors`. Eligible = a file that has
    completed Phase 1 layer selection (status is past
    `awaiting_layers`). Errored or still-discovering files are counted
    in `skipped` and don't get a worker dispatched.

    `file_id_filter` restricts the run to a subset (used by the
    startup auto-rescale migration); `None` runs the full set.
    """
    from app.files import FILE_STORE
    parent_id = str(uuid.uuid4())
    files = FILE_STORE.list_all()
    if file_id_filter is not None:
        files = [r for r in files if r.id in file_id_filter]
    eligible = [r for r in files if r.status not in _REPROCESS_SKIP_STATUSES]
    skipped = len(files) - len(eligible)
    now = time.time()
    with _lock:
        _jobs[parent_id] = {
            "id": parent_id,
            "kind": kind,
            "status": "running" if eligible else "done",
            "submitted_at": now,
            "started_at": now,
            "completed_at": None if eligible else now,
            "total": len(eligible),
            "done": 0,
            "skipped": skipped,
            "errors": [],
        }
    if not eligible:
        return parent_id

    overrides_snap = _current_dev_overrides() or None
    for rec in eligible:
        fut = _get_executor().submit(
            _preprocess_worker,
            rec.id,
            str(upload_path(rec.id)),
            str(parsed_path(rec.id)),
            str(prematch_path(rec.id)),
            rec.library_id,
            list(rec.selected_layers) if rec.selected_layers is not None else None,
            None,
            overrides_snap,
        )
        fut.add_done_callback(
            lambda f, fid=rec.id, pid=parent_id: _on_reprocess_step_done(pid, fid, f)
        )
    return parent_id


def _on_reprocess_step_done(parent_id: str, file_id: str, fut: Future) -> None:
    from app.files import FILE_STORE
    try:
        result = fut.result()
    except Exception as exc:
        tb = traceback.format_exc()
        FILE_STORE.update_status(file_id, "error", error=f"{exc}\n{tb}")
        with _lock:
            job = _jobs.get(parent_id)
            if job is not None:
                job["errors"].append({"file_id": file_id, "error": str(exc)})
                job["done"] += 1
                if job["done"] >= job["total"]:
                    job["status"] = "done"
                    job["completed_at"] = time.time()
        return
    factor_changed = FILE_STORE.update_parsed(
        file_id,
        primitive_count=result["primitive_count"],
        bbox=tuple(result["bbox"]) if result["bbox"] else (0, 0, 0, 0),
        background=result["background"],
        insunits=result.get("insunits"),
        applied_scale=float(result.get("applied_scale", 1.0)),
    )
    if factor_changed:
        _invalidate_match_after_rescale(file_id)
    _maybe_clear_redundant_unit_override(file_id, result)
    with _lock:
        job = _jobs.get(parent_id)
        if job is not None:
            job["done"] += 1
            if job["done"] >= job["total"]:
                job["status"] = "done"
                job["completed_at"] = time.time()


# ---- Reads ----------------------------------------------------------------
def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def list_jobs() -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values()]


def latest_rule_check_job(product_id: str) -> dict | None:
    """Latest rule-check job dict for a product, or None. Used by
    `GET /api/products` so a fresh dashboard load can pick up a job
    that was kicked off in a previous browser session and is either
    still running or finished while the user was elsewhere."""
    latest: dict | None = None
    with _lock:
        for j in _jobs.values():
            if j.get("kind") != "rule_check":
                continue
            if j.get("product_id") != product_id:
                continue
            if latest is None or (j.get("submitted_at") or 0) > (latest.get("submitted_at") or 0):
                latest = j
        return dict(latest) if latest else None
