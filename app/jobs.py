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

WORKER STORE-ACCESS INVARIANT
-----------------------------
Worker functions (`_preprocess_worker`, `_save_match_worker`,
`_rule_check_worker`, `_discover_layers_worker`) MUST load library/template
state with a fresh ``Store.load_library(...)`` read. They MUST NOT read the
process-level ``LIBRARIES`` cache: that singleton is seeded only by in-process
``add_template`` mutations, which never happen inside a reused worker process,
so ``LIBRARIES.get`` returns a stale snapshot on every job after the first one
handled by that worker — silently dropping newly-committed templates from the
Match JSON. (A regression test guards against ``LIBRARIES.get`` appearing in
worker code; see tests.) The long-form rationale lives in `_save_match_worker`.
"""

from __future__ import annotations

import json
import logging
import os
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
    match_path,
    parsed_path,
    prematch_path,
    rule_check_path,
    upload_path,
)


logger = logging.getLogger(__name__)

MAX_WORKERS = int(os.environ.get("SMDR2_MAX_WORKERS", "2"))

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
    product_id: str | None = None,
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
    recover_notes: dict[str, Any] | None = None
    if use_cache:
        with open(transient_primitives) as f:
            cached = json.load(f)
        primitives = cached["primitives"]
        bbox = tuple(cached["bbox"]) if cached.get("bbox") else None
        background = cached.get("background", "#ffffff")
        insunits = cached.get("insunits")
        applied_scale = float(cached.get("applied_scale", 1.0))
        # Phase 1 stashes the recover audit if it used the recover path;
        # propagate it so Phase 2's persistence path keeps the same notes
        # even when we never re-open the DXF in this process.
        cached_notes = cached.get("recover_notes")
        if isinstance(cached_notes, dict):
            recover_notes = cached_notes
    else:
        out = flatten_for_render(
            src, user_unit_override=user_unit_override, file_id=file_id,
        )
        primitives = out.primitives
        bbox = out.bbox
        background = out.background
        insunits = out.insunits
        applied_scale = out.applied_scale
        recover_notes = out.recover_notes

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
    #
    # We build the per-template `out` dict keyed by snake-class, then collapse
    # to the flat `by_class: {display_name: [handle, ...]}` shape the prematch
    # JSON contract expects. NB: preprocess has no side rects yet, so view
    # constraints aren't enforced here and same-radius cross-fire is not
    # disambiguated at this stage — the class-toolbar chips can over-count
    # until the operator draws side regions and runs scan-all / save-match
    # (where the view split resolves it). With distinct BGA/fiducial radii
    # there is no cross-fire and the counts are already clean.
    from app.side_regions import parse_match_key
    from app.library import CLASS_JSON_KEY

    store = Store(DB_PATH)
    classes, configs_by_class, templates_by_class = store.load_library(
        library_id, product_id=product_id
    )
    out: dict[str, list[list[str]]] = {}
    # Iterate the `classes` list (deterministic order) and look up
    # templates via `.get(cls, [])` — same pattern `_save_match_worker`
    # uses, lets test fakes hook the dict to drive their per-class
    # find_matches stub.
    for cls_name in classes:
        cfg = configs_by_class.get(cls_name) or {}
        strategy = cfg.get("match_strategy") or "chamfer"
        bbox_ratio = cfg.get("bbox_ratio")
        for idx, tmpl in enumerate(templates_by_class.get(cls_name, [])):
            result = find_matches_from_pointsets(
                tmpl.entity_point_sets, shapes,
                entity_kinds=tmpl.entity_kinds,
                strategy=strategy,
                bbox_ratio=bbox_ratio,
            )
            json_cls = CLASS_JSON_KEY.get(cls_name, cls_name)
            base_key = f"{json_cls}.{idx}"
            # Each match instance is one handle-list. No side-view
            # prefix at preprocess time — side regions get drawn later,
            # so we keep keys unprefixed (snake-class only).
            for m in result.matches:
                out.setdefault(base_key, []).append(list(m.handles))

    # Collapse `out` (keys like `bga_ball.0`) back to the flat
    # display-name → handles shape the prematch JSON contract exposes.
    # Reverse the snake → display mapping; classes without an entry in
    # `CLASS_JSON_KEY` use their display name verbatim. No cross-fire
    # resolution runs here: BGABall/FiducialCircle are disambiguated by
    # mutually exclusive view constraints at save-match/scan-all once side
    # regions are drawn (preprocess has no rects yet).
    display_by_snake = {v: k for k, v in CLASS_JSON_KEY.items()}
    by_class_sets: dict[str, set[str]] = {}
    for key, instance_lists in out.items():
        parsed = parse_match_key(key)
        if parsed is None:
            continue
        _prefix, cls_snake, _idx = parsed
        cls_display = display_by_snake.get(cls_snake, cls_snake)
        bucket = by_class_sets.setdefault(cls_display, set())
        for hl in instance_lists:
            bucket.update(hl)
    by_class: dict[str, list[str]] = {
        k: sorted(v) for k, v in by_class_sets.items()
    }

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
        "dxf_recover_notes": recover_notes,
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
    product_id: str | None = None,
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
        product_id,
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
        product_id=rec.product_id,
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
        logger.warning(
            "preprocess_failed job_id=%s file_id=%s %s: %s",
            job_id, file_id, type(e).__name__, e,
        )
        return
    # Post-result work (FILE_STORE mutations) runs inside the guard so an
    # exception here is logged and flips the job to `error` instead of being
    # silently swallowed after a premature `done`.
    try:
        factor_changed = FILE_STORE.update_parsed(
            file_id,
            primitive_count=result["primitive_count"],
            bbox=tuple(result["bbox"]) if result["bbox"] else (0, 0, 0, 0),
            background=result["background"],
            insunits=result.get("insunits"),
            applied_scale=float(result.get("applied_scale", 1.0)),
        )
        FILE_STORE.set_dxf_recover_notes(file_id, result.get("dxf_recover_notes"))
        if factor_changed:
            _invalidate_match_after_rescale(file_id)
        _maybe_clear_redundant_unit_override(file_id, result)
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = f"{e}\n{tb}"
            job["completed_at"] = time.time()
        logger.error(
            "preprocess_callback_failed job_id=%s file_id=%s %s: %s",
            job_id, file_id, type(e).__name__, e, exc_info=True,
        )
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    logger.info(
        "preprocess_done job_id=%s file_id=%s primitive_count=%s",
        job_id, file_id, result.get("primitive_count"),
    )


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

    out = flatten_for_render(src, file_id=file_id)
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
    # The `recover_notes` carry-over lets Phase 2 persist the audit summary
    # without re-opening the DXF when it reuses this cache.
    (preview / "primitives.json").write_text(json.dumps({
        "primitives": out.primitives,
        "bbox": out.bbox,
        "background": out.background,
        "insunits": out.insunits,
        "applied_scale": out.applied_scale,
        "recover_notes": out.recover_notes,
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
    product_id = job.get("product_id")
    try:
        result = fut.result()
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = f"{e}\n{tb}"
            job["completed_at"] = time.time()
        logger.warning(
            "rule_check_failed job_id=%s product_id=%s %s: %s",
            job_id, product_id, type(e).__name__, e,
        )
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    logger.info(
        "rule_check_done job_id=%s product_id=%s pass_count=%s",
        job_id, product_id, result.get("pass_count"),
    )


# ---- Save Match worker --------------------------------------------------
def _save_match_worker(file_id: str, dst: str) -> dict[str, Any]:
    # Imports inside so spawned workers re-import cleanly. This worker
    # is a verbatim port of the loop that previously lived inside
    # `app/main.py:save_match_json` — same `out` shape, same per-class
    # skip-when-impossible + view-split guard, same on-disk JSON layout.
    # Only the surrounding plumbing (HTTP handler → process pool) changes.
    #
    # IMPORTANT: read the library via `Store.load_library(...)` rather
    # than `LIBRARIES.get(...)`. The `LIBRARIES` singleton caches a
    # per-process `Library` instance whose `_templates` dict is only
    # ever updated by in-process `add_template` calls — in this worker
    # process, those mutations never happen (templates are added in the
    # parent FastAPI process). Using the cache here means: the FIRST
    # save_match job in a given worker process snapshots whatever was
    # in the DB then, and EVERY subsequent job in the same worker sees
    # that stale snapshot. The symptoms reported in production:
    #   - first save → match.json is `{}` (cache seeded empty)
    #   - later saves → match.json is missing newly-added classes
    # The `_preprocess_worker` already follows this fresh-load pattern;
    # we mirror it here.
    from app.files import FILE_STORE
    from app.library import (
        CLASS_JSON_KEY,
        CLASS_VIEW_CONSTRAINTS,
        Store,
        build_handle_index,
    )
    from app.matching import build_entity_shapes, find_matches_from_pointsets
    from app.side_regions import split_matches_by_side
    from app.storage import DB_PATH

    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise RuntimeError(f"file {file_id!r} not found in worker")
    store = Store(DB_PATH)
    if store.get_library(rec.library_id) is None:
        raise RuntimeError(
            f"library {rec.library_id!r} not registered in worker"
        )
    classes, configs_by_class, templates_by_class = store.load_library(
        rec.library_id, product_id=rec.product_id
    )
    pp = parsed_path(file_id)
    if not pp.exists():
        raise RuntimeError(f"parsed file missing for {file_id!r}: {pp}")
    with open(pp) as f:
        parsed = json.load(f)
    hi = build_handle_index(parsed["primitives"])
    shapes = build_entity_shapes(parsed["primitives"], hi)

    out: dict[str, list[list[str]]] = {}
    total_matches = 0
    side_counts = {
        "top_view": 0, "bottom_view": 0, "side_view": 0,
        "unassigned": 0, "dropped": 0,
    }
    rect_for = {
        "top_view": rec.top_view_rect,
        "bottom_view": rec.bottom_view_rect,
        "side_view": rec.side_view_rect,
    }
    for cls_name in classes:
        allowed = CLASS_VIEW_CONSTRAINTS.get(cls_name)
        if allowed is not None and not any(
            rect_for[v] is not None for v in allowed
        ):
            continue
        cfg = configs_by_class.get(cls_name) or {}
        strategy = cfg.get("match_strategy") or "chamfer"
        bbox_ratio = cfg.get("bbox_ratio")
        for idx, tmpl in enumerate(templates_by_class.get(cls_name, [])):
            result = find_matches_from_pointsets(
                tmpl.entity_point_sets, shapes,
                entity_kinds=tmpl.entity_kinds,
                strategy=strategy, bbox_ratio=bbox_ratio,
            )
            json_cls = CLASS_JSON_KEY.get(cls_name, cls_name)
            base_key = f"{json_cls}.{idx}"
            grouped, cnts = split_matches_by_side(
                base_key, result.matches, shapes,
                rec.top_view_rect, rec.bottom_view_rect, rec.side_view_rect,
                class_name=cls_name,
            )
            for k, v in grouped.items():
                out.setdefault(k, []).extend(v)
            for k, n in cnts.items():
                side_counts[k] += n
            total_matches += len(result.matches)

    # BGABall/FiducialCircle (and any same-geometry pair) are disambiguated
    # by the mutually exclusive view constraints applied in
    # split_matches_by_side above — no post-match arbitration step.
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "w") as f:
        json.dump(out, f, indent=2)
    try:
        saved_to = str(dst_path.relative_to(DATA_DIR.parent))
    except ValueError:
        saved_to = str(dst_path)
    return {
        "file_id": file_id,
        "library_id": rec.library_id,
        "template_keys": list(out.keys()),
        "total_matches": total_matches,
        "side_counts": side_counts,
        "saved_to": saved_to,
        "match_saved": True,
    }


def submit_save_match(file_id: str) -> str:
    """Submit a per-file Match JSON build to the worker pool. Returns
    the job_id immediately; the request handler should return 202 +
    {job_id} so the front-end can poll `GET /api/jobs/{job_id}`.

    The worker is portable across processes — it re-opens `FILE_STORE`,
    re-loads `LIBRARIES`, and reads `parsed/{file_id}.json` from disk.
    `file.match_saved` is flipped only in `_on_save_match_done`."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "file_id": file_id,
            "kind": "save_match",
            "status": "queued",
            "submitted_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
    fut = _get_executor().submit(
        _save_match_worker,
        file_id,
        str(match_path(file_id)),
    )
    fut.add_done_callback(lambda f: _on_save_match_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


def _on_save_match_done(job_id: str, fut: Future) -> None:
    from app.files import FILE_STORE
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
            job["error"] = f"{e}\n{tb}"
            job["completed_at"] = time.time()
        logger.warning(
            "save_match_failed job_id=%s file_id=%s %s: %s",
            job_id, file_id, type(e).__name__, e,
        )
        return
    # Flag the file ready for product-level rule checking. Only after
    # the JSON is on disk — on worker error this stays untouched so the
    # rule-check submit gate keeps rejecting the role. Run inside the guard
    # so a mutation failure flips the job to `error` rather than being
    # swallowed after a premature `done`.
    try:
        FILE_STORE.set_match_saved(file_id, True)
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = f"{e}\n{tb}"
            job["completed_at"] = time.time()
        logger.error(
            "save_match_callback_failed job_id=%s file_id=%s %s: %s",
            job_id, file_id, type(e).__name__, e, exc_info=True,
        )
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    logger.info("save_match_done job_id=%s file_id=%s", job_id, file_id)


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
    FILE_STORE.set_dxf_recover_notes(file_id, result.get("dxf_recover_notes"))
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
