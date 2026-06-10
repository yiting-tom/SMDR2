"""Background job queue (ProcessPoolExecutor + in-memory job dict).

Two job kinds, both running on the same worker pool:

- **discover**: cheap first pass — parse DXF, enumerate layers, render a
  per-layer SVG preview, drop the full primitive set into a transient file
  so Phase 2 can skip re-parsing. Outputs:
      data/layer_preview/{version_id}/{file_id}/layers.json
      data/layer_preview/{version_id}/{file_id}/<safe_name>.svg
      data/layer_preview/{version_id}/{file_id}/primitives.json   (transient)
- **preprocess**: existing heavy pipeline, now filtered to the user's
  chosen layer subset. Outputs (as before):
      data/parsed/{version_id}/{file_id}.json
      data/prematch/{version_id}/{file_id}.json

Every job operates on a (version_id, file_id) binding — the same DXF
bytes bound into two versions parse/match independently, and artifacts
key on the pair so one version's re-run never clobbers another's.

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
    layer_preview_dir,
    layer_preview_primitives_path,
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
    version_id: str,
    file_id: str,
    src: str,
    parsed_dst: str,
    prematch_dst: str,
    library_id: str,
    selected_layers: list[str] | None = None,
    transient_primitives: str | None = None,
    dev_overrides_snapshot: dict[str, Any] | None = None,
    user_unit_override: str | None = None,
    layout_name: str | None = None,
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
    # The Phase-1 transient cache is rendered from the SAME `chosen_layout`
    # as this preprocess (both resolve it off the file row), so reusing it
    # is layout-consistent — no extra cache key needed for `layout_name`.
    use_cache = (
        user_unit_override is None
        and transient_primitives
        and Path(transient_primitives).exists()
    )
    recover_notes: dict[str, Any] | None = None
    source_layout: str | None = None
    source_is_paperspace: bool = False
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
        source_layout = cached.get("source_layout")
        source_is_paperspace = bool(cached.get("source_is_paperspace", False))
    else:
        out = flatten_for_render(
            src, user_unit_override=user_unit_override, file_id=file_id,
            layout_name=layout_name,
        )
        primitives = out.primitives
        bbox = out.bbox
        background = out.background
        insunits = out.insunits
        applied_scale = out.applied_scale
        recover_notes = out.recover_notes
        source_layout = out.source_layout
        source_is_paperspace = out.source_is_paperspace

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
                "source_layout": source_layout,
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
    classes, configs_by_class, templates_by_class = store.load_library(library_id)
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
        "version_id": version_id,
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
        "source_layout": source_layout,
        "source_is_paperspace": source_is_paperspace,
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
    version_id: str,
    file_id: str,
    library_id: str,
    selected_layers: list[str] | None = None,
    user_unit_override: str | None = None,
    layout_name: str | None = None,
) -> str:
    """Submit a preprocess job for one (version, file) binding. When
    `user_unit_override` is None, the worker reads the binding's
    persisted override (set in a prior viewer-picker action). Callers
    that came in via the `/unit-override` endpoint pass the new unit
    explicitly so the job picks it up even before the row write commits.

    `layout_name` (which AutoCAD tab to render) is resolved the same
    way: None falls back to the binding's persisted `chosen_layout`."""
    # Worker runs in a separate process and can't trivially read the
    # row, so resolve the active override + chosen layout here.
    if user_unit_override is None or layout_name is None:
        from app.files import FILE_STORE
        rec = FILE_STORE.get(version_id, file_id)
        if rec is not None:
            if user_unit_override is None:
                user_unit_override = rec.user_unit_override
            if layout_name is None:
                layout_name = rec.chosen_layout
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "version_id": version_id,
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
    transient = layer_preview_primitives_path(version_id, file_id)
    fut = _get_executor().submit(
        _preprocess_worker,
        version_id,
        file_id,
        str(upload_path(file_id)),
        str(parsed_path(version_id, file_id)),
        str(prematch_path(version_id, file_id)),
        library_id,
        list(selected_layers) if selected_layers is not None else None,
        str(transient),
        _current_dev_overrides() or None,
        user_unit_override,
        layout_name,
    )
    fut.add_done_callback(lambda f: _on_preprocess_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


def find_inflight_preprocess_job(version_id: str, file_id: str) -> str | None:
    """Return the id of any queued / running preprocess job for the
    given binding, or None. Used by the unit-override endpoint to return
    `409 Conflict` instead of double-enqueueing."""
    with _lock:
        for job in _jobs.values():
            if (
                job.get("kind") == "preprocess"
                and job.get("file_id") == file_id
                and job.get("version_id") == version_id
                and job.get("status") in ("queued", "running")
            ):
                return job["id"]
    return None


def submit_unit_override_preprocess(version_id: str, file_id: str, unit: str) -> str:
    """Operator-driven recompute triggered by the viewer's unit picker.
    Writes the override to the binding, flips status to PREPROCESSING,
    and enqueues the standard preprocess pipeline carrying the new
    unit so the worker uses it directly. The done-callback may clear
    the override back to NULL if it agrees with the detector — see
    `_on_preprocess_done`."""
    from app.files import FILE_STORE, PREPROCESSING

    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise KeyError((version_id, file_id))
    FILE_STORE.set_user_unit_override(version_id, file_id, unit)
    FILE_STORE.update_status(version_id, file_id, PREPROCESSING)
    return submit_preprocess(
        version_id,
        file_id,
        library_id=_library_of_version(version_id),
        selected_layers=rec.selected_layers,
        user_unit_override=unit,
        layout_name=rec.chosen_layout,
    )


def _library_of_version(version_id: str) -> str:
    """Resolve a version's library id (fresh read, no cache)."""
    from app.versions import VERSION_STORE
    v = VERSION_STORE.get(version_id)
    if v is None:
        raise KeyError(f"version {version_id!r} not found")
    return v.library_id


# Backwards-compat alias — older code may still call submit_parse.
submit_parse = submit_preprocess


def _on_preprocess_done(job_id: str, fut: Future) -> None:
    from app.files import FILE_STORE  # local import to break cycle
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    version_id = job["version_id"]
    file_id = job["file_id"]
    try:
        result = fut.result()
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = str(e)
            job["completed_at"] = time.time()
        FILE_STORE.update_status(version_id, file_id, "error", error=f"{e}\n{tb}")
        logger.warning(
            "preprocess_failed job_id=%s version_id=%s file_id=%s %s: %s",
            job_id, version_id, file_id, type(e).__name__, e,
        )
        return
    # Post-result work (FILE_STORE mutations) runs inside the guard so an
    # exception here is logged and flips the job to `error` instead of being
    # silently swallowed after a premature `done`.
    try:
        factor_changed = FILE_STORE.update_parsed(
            version_id,
            file_id,
            primitive_count=result["primitive_count"],
            bbox=tuple(result["bbox"]) if result["bbox"] else (0, 0, 0, 0),
            background=result["background"],
            insunits=result.get("insunits"),
            applied_scale=float(result.get("applied_scale", 1.0)),
        )
        FILE_STORE.set_dxf_recover_notes(file_id, result.get("dxf_recover_notes"))
        _persist_source_layout(version_id, file_id, result)
        if factor_changed:
            _invalidate_match_after_rescale(version_id, file_id)
        _maybe_clear_redundant_unit_override(version_id, file_id, result)
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = f"{e}\n{tb}"
            job["completed_at"] = time.time()
        logger.error(
            "preprocess_callback_failed job_id=%s version_id=%s file_id=%s %s: %s",
            job_id, version_id, file_id, type(e).__name__, e, exc_info=True,
        )
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    logger.info(
        "preprocess_done job_id=%s version_id=%s file_id=%s primitive_count=%s",
        job_id, version_id, file_id, result.get("primitive_count"),
    )


def _invalidate_match_after_rescale(version_id: str, file_id: str) -> None:
    """Drop the binding's saved Match JSON when `applied_scale` changes.
    Saved point sets reference the prior coordinate system; rerunning is
    cheaper and safer than scaling the JSON in place. See the
    `dxf-pipeline` spec / `auto-normalize-unit-suspect-dxf` change."""
    from app.files import FILE_STORE
    from app.storage import match_path

    mp = match_path(version_id, file_id)
    try:
        mp.unlink()
    except FileNotFoundError:
        pass
    FILE_STORE.set_match_saved(version_id, file_id, False)


def _persist_source_layout(version_id: str, file_id: str, result: dict[str, Any]) -> None:
    """Auto-stamp the binding's `chosen_layout` when preprocess rendered
    from a paper-space layout (modelspace was empty, so flatten fell back
    to a layout tab). This pins the tab for every future re-preprocess and
    feeds the dashboard's "tab: <name>" badge. Modelspace sources leave
    `chosen_layout` untouched so NULL stays the clean default."""
    from app.files import FILE_STORE
    if not result.get("source_is_paperspace"):
        return
    source_layout = result.get("source_layout")
    if source_layout:
        FILE_STORE.set_chosen_layout(version_id, file_id, source_layout)


def _maybe_clear_redundant_unit_override(
    version_id: str, file_id: str, result: dict[str, Any],
) -> None:
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
        FILE_STORE.set_user_unit_override(version_id, file_id, None)


# ---- Discover-layers worker (Phase 1) ------------------------------------
def _build_layout_picker(
    src: str,
    file_id: str,
    preview: Path,
    content_layouts: list[dict[str, Any]],
) -> int:
    """Render one SVG thumbnail per candidate AutoCAD tab and write the
    layout manifest. Used only when a DXF's geometry lives in >1 paper-space
    layout (model space empty) and the operator must choose which tab to
    load. Returns the number of tabs actually offered.

    Each candidate is flattened and tabs that produce ZERO primitives are
    dropped — the `entity_count` proxy can flag a tab as "content" that
    renders empty (e.g. TEXT-only under TextPolicy.IGNORE), and offering a
    blank tab is a dead-end. The manifest is written only when at least two
    tabs render something; otherwise nothing is written and the caller falls
    through to layer discovery on the auto-resolved tab (return value < 2
    signals this). Manifest + thumbnails live in the `layouts/` subdir so
    they survive the later layer-discovery rewrite (which only touches files
    in the parent dir) — letting the operator re-open the picker."""
    from app.dxf import flatten_for_render, render_layer_svg, sanitize_layer_name

    rendered_entries: list[tuple[str, dict[str, Any]]] = []
    for layout in content_layouts:
        name = layout["name"]
        rendered = flatten_for_render(src, file_id=file_id, layout_name=name)
        if not rendered.primitives:
            continue  # renders empty — not a useful pick
        safe = sanitize_layer_name(name)
        svg = render_layer_svg(
            rendered.primitives,
            list(range(len(rendered.primitives))),
            rendered.bbox,
            background=rendered.background,
        )
        rendered_entries.append((svg, {
            "name": name,
            "safe_name": safe,
            "svg_filename": f"{safe}.svg",
            "entity_count": layout["entity_count"],
            "is_paperspace": layout["is_paperspace"],
        }))
    if len(rendered_entries) < 2:
        # Not genuinely ambiguous — don't write a manifest (keeps
        # has_layout_options false and lets the worker proceed normally).
        return len(rendered_entries)
    layouts_dir = preview / "layouts"
    layouts_dir.mkdir(parents=True, exist_ok=True)
    for svg, entry in rendered_entries:
        (layouts_dir / entry["svg_filename"]).write_text(svg)
    manifest = {"file_id": file_id, "layouts": [e for _, e in rendered_entries]}
    (layouts_dir / "layouts.json").write_text(json.dumps(manifest))
    return len(rendered_entries)


def _discover_layers_worker(
    file_id: str,
    src: str,
    preview_dir: str,
    layout_name: str | None = None,
) -> dict[str, Any]:
    """Parse a DXF once, render per-layer SVG thumbnails, and persist the
    full primitive set for Phase 2 reuse. Returns a manifest summary.

    `layout_name` selects which AutoCAD tab to flatten (None = auto: model
    space, or the richest paper-space layout when model space is empty).

    Layout gate: when the operator has NOT already picked a tab
    (`layout_name is None`) and auto-resolution fell back to a paper-space
    layout (`out.source_is_paperspace`), we check whether the geometry is
    spread across more than one paper-space layout. If so this is genuinely
    ambiguous — we render a layout picker and return `needs_layout_pick`
    instead of a layer manifest, so the file parks in `awaiting_layout`.
    A single content layout is unambiguous: auto-resolution already chose
    it, so we proceed straight to layer discovery. This keeps the common
    case (geometry in model space) at exactly one DXF open with zero extra
    work — the enumerate probe only runs on the paper-space-fallback path."""
    from app.dxf import (
        enumerate_layouts,
        flatten_for_render,
        group_primitives_by_layer,
        render_layer_svg,
        sanitize_layer_name,
    )

    out = flatten_for_render(src, file_id=file_id, layout_name=layout_name)

    preview = Path(preview_dir)
    preview.mkdir(parents=True, exist_ok=True)

    if layout_name is None and out.source_is_paperspace:
        inventory = enumerate_layouts(src, file_id=file_id)
        content_paper = [
            L for L in inventory
            if L["is_paperspace"] and L["entity_count"] > 0
        ]
        # `_build_layout_picker` flattens each candidate and only commits a
        # manifest when >= 2 tabs actually render; a return < 2 means the
        # ambiguity dissolved (viewport-/text-only tabs), so fall through to
        # layer discovery on the already auto-resolved tab (`out`).
        if len(content_paper) >= 2:
            n = _build_layout_picker(src, file_id, preview, content_paper)
            if n >= 2:
                return {
                    "file_id": file_id,
                    "needs_layout_pick": True,
                    "layout_count": n,
                }

    by_layer = group_primitives_by_layer(out.primitives)

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
        "source_layout": out.source_layout,
    }
    (preview / "layers.json").write_text(json.dumps(manifest))

    # Transient primitives cache — Phase 2 picks it up to skip re-parsing.
    # The `recover_notes` carry-over lets Phase 2 persist the audit summary
    # without re-opening the DXF when it reuses this cache; `source_layout`
    # lets Phase 2 stamp the "tab: <name>" badge without re-opening either.
    (preview / "primitives.json").write_text(json.dumps({
        "primitives": out.primitives,
        "bbox": out.bbox,
        "background": out.background,
        "insunits": out.insunits,
        "applied_scale": out.applied_scale,
        "recover_notes": out.recover_notes,
        "source_layout": out.source_layout,
        "source_is_paperspace": out.source_is_paperspace,
    }))

    return {
        "file_id": file_id,
        "layer_count": len(layers),
        "bbox": out.bbox,
        "background": out.background,
        "source_layout": out.source_layout,
    }


def submit_discover_layers(
    version_id: str, file_id: str, layout_name: str | None = None,
) -> str:
    """Kick off Phase 1 in the worker pool. The binding moves to
    `awaiting_layers` once the manifest is ready — or to `awaiting_layout`
    when the worker detects geometry spread across multiple paper-space
    layouts and needs the operator to pick a tab first.

    `layout_name` is resolved from the binding's persisted `chosen_layout`
    when None, so a re-run after a layout pick (or for a tab-bound file)
    renders the right tab and skips the picker gate."""
    if layout_name is None:
        from app.files import FILE_STORE
        rec = FILE_STORE.get(version_id, file_id)
        if rec is not None:
            layout_name = rec.chosen_layout
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "version_id": version_id,
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
        str(layer_preview_dir(version_id, file_id)),
        layout_name,
    )
    fut.add_done_callback(lambda f: _on_discover_done(job_id, f))
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()
    return job_id


def _on_discover_done(job_id: str, fut: Future) -> None:
    from app.files import AWAITING_LAYERS, AWAITING_LAYOUT, ERROR, FILE_STORE
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return
    version_id = job["version_id"]
    file_id = job["file_id"]
    try:
        result = fut.result()
    except Exception as e:
        tb = traceback.format_exc()
        with _lock:
            job["status"] = "error"
            job["error"] = str(e)
            job["completed_at"] = time.time()
        FILE_STORE.update_status(version_id, file_id, ERROR, error=f"{e}\n{tb}")
        return
    with _lock:
        job["status"] = "done"
        job["completed_at"] = time.time()
        job["result"] = result
    # Geometry spread across multiple paper-space layouts → park for a tab
    # pick; otherwise proceed to the normal layer-selection gate.
    if result.get("needs_layout_pick"):
        FILE_STORE.update_status(version_id, file_id, AWAITING_LAYOUT)
    else:
        FILE_STORE.update_status(version_id, file_id, AWAITING_LAYERS)


# ---- Rule-check worker ---------------------------------------------------
def _rule_check_worker(
    product_id: str,
    version_id: str,
    file_ids: list[str],
    dst: str,
) -> dict[str, Any]:
    """Run a version's DRC in a worker process.

    The rule contract stays keyed by product (`check_rules(product_id,
    bundle_dir)` — rules are product-level, version-independent) but the
    bundle is built from the version's bindings and the result persists
    to `rule_check/{version_id}.json`.

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
        rec = FILE_STORE.get(version_id, fid)
        if rec is None:
            raise RuntimeError(
                f"binding ({version_id!r}, {fid!r}) not found in worker"
            )
        files.append(rec)
        if rec.dxf_role:
            roles_seen.add(rec.dxf_role)

    with materialise_bundle(product, version_id, files) as bundle_dir:
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
        "version_id": version_id,
        "saved_to": saved_to,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
        "roles_covered": sorted(roles_seen),
    }


def submit_rule_check(
    product_id: str,
    version_id: str,
    file_ids: list[str],
) -> str:
    """Submit a version's rule check to the worker pool. Returns
    the job_id immediately; the request handler should return 202 +
    {job_id} so the front-end can poll `GET /api/jobs/{job_id}`.

    The worker receives the product id (rule contract), the version id
    (binding + artifact scope), and the list of role-bound file ids; it
    re-opens the per-process stores to fetch records and materialise the
    bundle from on-disk DXF + Match JSON files."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "product_id": product_id,
            "version_id": version_id,
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
        version_id,
        list(file_ids),
        str(rule_check_path(version_id)),
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
def _save_match_worker(version_id: str, file_id: str, dst: str) -> dict[str, Any]:
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
    from app.side_regions import (
        parse_match_key,
        split_matches_by_side,
        suppress_contained_matches,
    )
    from app.storage import DB_PATH

    from app.versions import VersionStore

    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise RuntimeError(
            f"binding ({version_id!r}, {file_id!r}) not found in worker"
        )
    version = VersionStore(DB_PATH).get(version_id)
    if version is None:
        raise RuntimeError(f"version {version_id!r} not found in worker")
    store = Store(DB_PATH)
    if store.get_library(version.library_id) is None:
        raise RuntimeError(
            f"library {version.library_id!r} not registered in worker"
        )
    classes, configs_by_class, templates_by_class = store.load_library(
        version.library_id
    )
    pp = parsed_path(version_id, file_id)
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
    # Raw per-display-class handle union for the not-side-aware pre-match
    # snapshot we refresh after the scan (see the write below). Accumulated
    # before split_matches_by_side / suppression so it mirrors the shape
    # _preprocess_worker persists; the union is invariant to suppression.
    prematch_sets: dict[str, set[str]] = {}
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
            # Raw (pre-split, pre-suppression) handle union for the refreshed
            # pre-match snapshot. Only classes with ≥1 match get an entry, to
            # match _preprocess_worker's by_class shape.
            if result.matches:
                pm_bucket = prematch_sets.setdefault(cls_name, set())
                for m in result.matches:
                    pm_bucket.update(m.handles)
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
    #
    # Contained-match suppression: when one class holds both a partial
    # template (e.g. SMD mask-only) and a fuller one (mask+body), a body
    # location fires both and records the feature twice. Drop the instance
    # whose handle set is contained in a larger same-class instance's, so the
    # persisted Match JSON — which rule-check reads per instance — counts each
    # physical feature once. `total_matches` stays the raw matches found;
    # `side_counts` is recomputed from the survivors and `suppressed_count` is
    # reported, keeping `raw = survivors + dropped + suppressed`.
    pre_suppress = sum(len(v) for v in out.values())
    out = suppress_contained_matches(out)
    suppressed_count = pre_suppress - sum(len(v) for v in out.values())
    _dropped = side_counts["dropped"]
    side_counts = {
        "top_view": 0, "bottom_view": 0, "side_view": 0,
        "unassigned": 0, "dropped": _dropped,
    }
    for key, instances in out.items():
        parsed = parse_match_key(key)
        prefix = parsed[0] if parsed else None
        side_counts[prefix if prefix else "unassigned"] += len(instances)

    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "w") as f:
        # Compact separators: the rule-checker consumes this machine-to-machine,
        # and indent=2 was ~2.3x larger on 10k-instance (BGA) saves.
        json.dump(out, f, separators=(",", ":"))

    # Refresh the not-side-aware pre-match snapshot from this same live scan so
    # the auto-shown overlay on the next viewer load reflects the current
    # library (templates committed since preprocess included) instead of the
    # frozen preprocess-time snapshot. Mirrors the {by_class, total} contract
    # _preprocess_worker writes. Best-effort: a failure must not fail the Match
    # JSON already persisted above — worst case the previous (stale) snapshot
    # stays, i.e. no worse than before this change.
    try:
        pm_by_class = {k: sorted(v) for k, v in prematch_sets.items()}
        pm_path = Path(prematch_path(version_id, file_id))
        pm_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pm_path, "w") as f:
            json.dump(
                {
                    "by_class": pm_by_class,
                    "total": sum(len(v) for v in pm_by_class.values()),
                },
                f,
            )
    except Exception:
        logger.warning(
            "save_match: failed to refresh pre-match snapshot for %s",
            file_id, exc_info=True,
        )

    try:
        saved_to = str(dst_path.relative_to(DATA_DIR.parent))
    except ValueError:
        saved_to = str(dst_path)
    return {
        "version_id": version_id,
        "file_id": file_id,
        "library_id": version.library_id,
        "template_keys": list(out.keys()),
        "total_matches": total_matches,
        "side_counts": side_counts,
        "suppressed_count": suppressed_count,
        "saved_to": saved_to,
        "match_saved": True,
    }


def submit_save_match(version_id: str, file_id: str) -> str:
    """Submit a Match JSON build for one binding to the worker pool.
    Returns the job_id immediately; the request handler should return
    202 + {job_id} so the front-end can poll `GET /api/jobs/{job_id}`.

    The worker is portable across processes — it re-opens the stores
    fresh and reads `parsed/{version_id}/{file_id}.json` from disk.
    `match_saved` is flipped only in `_on_save_match_done`."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "version_id": version_id,
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
        version_id,
        file_id,
        str(match_path(version_id, file_id)),
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
    version_id = job["version_id"]
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
        FILE_STORE.set_match_saved(version_id, file_id, True)
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
    "awaiting_layout",
    "awaiting_layers",
    "error",
})


def submit_reprocess_all(*, kind: str = "reprocess-all") -> str:
    """Re-preprocess every eligible binding in storage with current
    overrides, iterating versions (each binding parses against its own
    version's library and writes its own version-scoped artifacts).

    Returns one parent job_id; `_jobs[job_id]` exposes
    `total`, `done`, `skipped`, `errors`. Eligible = a binding that has
    completed Phase 1 layer selection (status is past
    `awaiting_layers`). Errored or still-discovering bindings are counted
    in `skipped` and don't get a worker dispatched. Bindings on
    signed-off versions are skipped too (frozen results must not change).
    """
    from app.files import FILE_STORE
    from app.versions import VERSION_STORE
    parent_id = str(uuid.uuid4())
    bindings = FILE_STORE.list_all()
    eligible = []
    skipped = 0
    lib_by_version: dict[str, str | None] = {}
    for r in bindings:
        if r.status in _REPROCESS_SKIP_STATUSES:
            skipped += 1
            continue
        if r.version_id not in lib_by_version:
            v = VERSION_STORE.get(r.version_id)
            lib_by_version[r.version_id] = (
                None if v is None or v.is_signed_off else v.library_id
            )
        if lib_by_version[r.version_id] is None:
            skipped += 1
            continue
        eligible.append(r)
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
            rec.version_id,
            rec.id,
            str(upload_path(rec.id)),
            str(parsed_path(rec.version_id, rec.id)),
            str(prematch_path(rec.version_id, rec.id)),
            lib_by_version[rec.version_id],
            list(rec.selected_layers) if rec.selected_layers is not None else None,
            None,                     # transient_primitives: re-parse from source
            overrides_snap,           # dev_overrides_snapshot
            rec.user_unit_override,   # honour the operator's unit, skip the detector
            rec.chosen_layout,        # keep rendering the operator's chosen tab
        )
        fut.add_done_callback(
            lambda f, vid=rec.version_id, fid=rec.id, pid=parent_id:
                _on_reprocess_step_done(pid, vid, fid, f)
        )
    return parent_id


def _on_reprocess_step_done(
    parent_id: str, version_id: str, file_id: str, fut: Future,
) -> None:
    from app.files import FILE_STORE
    try:
        result = fut.result()
    except Exception as exc:
        tb = traceback.format_exc()
        FILE_STORE.update_status(version_id, file_id, "error", error=f"{exc}\n{tb}")
        with _lock:
            job = _jobs.get(parent_id)
            if job is not None:
                job["errors"].append({
                    "version_id": version_id, "file_id": file_id,
                    "error": str(exc),
                })
                job["done"] += 1
                if job["done"] >= job["total"]:
                    job["status"] = "done"
                    job["completed_at"] = time.time()
        return
    factor_changed = FILE_STORE.update_parsed(
        version_id,
        file_id,
        primitive_count=result["primitive_count"],
        bbox=tuple(result["bbox"]) if result["bbox"] else (0, 0, 0, 0),
        background=result["background"],
        insunits=result.get("insunits"),
        applied_scale=float(result.get("applied_scale", 1.0)),
    )
    FILE_STORE.set_dxf_recover_notes(file_id, result.get("dxf_recover_notes"))
    _persist_source_layout(version_id, file_id, result)
    if factor_changed:
        _invalidate_match_after_rescale(version_id, file_id)
    _maybe_clear_redundant_unit_override(version_id, file_id, result)
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


def latest_rule_check_job(version_id: str) -> dict | None:
    """Latest rule-check job dict for a version, or None. Used by
    `GET /api/products` so a fresh dashboard load can pick up a job
    that was kicked off in a previous browser session and is either
    still running or finished while the user was elsewhere."""
    latest: dict | None = None
    with _lock:
        for j in _jobs.values():
            if j.get("kind") != "rule_check":
                continue
            if j.get("version_id") != version_id:
                continue
            if latest is None or (j.get("submitted_at") or 0) > (latest.get("submitted_at") or 0):
                latest = j
        return dict(latest) if latest else None
