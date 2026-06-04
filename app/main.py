"""SMDR2 FastAPI entry — multi-file workflow.

Routes:
    GET  /                           dashboard (file list + upload)
    GET  /viewer/{file_id}           viewer page for one file
    POST /api/files                  upload one or more DXFs
    GET  /api/files                  list files
    GET  /api/files/{file_id}        file metadata
    GET  /api/jobs/{job_id}          job status
    GET  /api/classes                template library summary
    GET  /api/files/{file_id}/primitives
    POST /api/files/{file_id}/match
    POST /api/files/{file_id}/commit
    GET  /api/files/{file_id}/scan-all
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from app import jobs
from app.files import (
    AWAITING_LAYERS,
    DISCOVERING_LAYERS,
    FILE_STORE,
    FileRecord,
    PREPROCESSING,
    READY,
)
from app.library import (
    CLASS_JSON_KEY,
    CLASS_VIEW_CONSTRAINTS,
    DEFAULT_LIBRARY_ID,
    LIBRARIES,
    Template,
    build_handle_index,
    collect_entity_kinds,
    collect_entity_points,
    is_product_scoped,
)
from app.matching import (
    EntityShape,
    build_entity_shapes,
    diagnose_swap,
    find_matches,
    find_matches_from_pointsets,
)
from app.products import PRODUCT_STORE, VALID_ROLES, Product
from app.drc_bundle import build_bundle
from app.side_regions import normalise_rect, parse_match_key, split_matches_by_side
from app.storage import (
    DATA_DIR,
    layer_manifest_path,
    layer_preview_svg_path,
    match_path,
    parsed_path,
    prematch_path,
    rule_check_path,
    upload_path,
)

TEST_DXF = DATA_DIR / "test.dxf"


# Max upload size for a single DXF (env-overridable, mirrors SMDR2_N_JOBS /
# SMDR2_MAX_WORKERS). 300 MB is generous for packaging DXFs and catches the
# "wrong file / corrupted export" mistake without web-scale machinery.
MAX_UPLOAD_BYTES = int(os.environ.get("SMDR2_MAX_UPLOAD_MB", "300")) * 1024 * 1024


def _load_json_or_http(path: str | Path, *, kind: str) -> dict:
    """Read a persisted pipeline-artifact JSON file, turning corruption into
    a clean HTTP 400 (with the artifact kind + path in the detail) instead of
    an uncaught JSONDecodeError → opaque 500. Matches the 400 convention used
    by `upload_product_rule_check`.

    MUST be called at the route-handler level, NOT inside an `@lru_cache`-wrapped
    helper: `lru_cache` memoizes raised exceptions, so caching the raise would
    re-raise the stale error on later cache hits with the same key.
    """
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{kind} file is unreadable/corrupt: {path}: {exc}",
        )


# ---- Cached parsed-JSON + shape index ------------------------------------
@lru_cache(maxsize=4)
def _cached_parsed(path: str, mtime_ns: int) -> dict:  # noqa: ARG001
    with open(path) as f:
        return json.load(f)


@lru_cache(maxsize=4)
def _cached_shapes(path: str, mtime_ns: int) -> tuple[dict[str, list[int]], dict[str, EntityShape]]:  # noqa: ARG001
    data = _cached_parsed(path, mtime_ns)
    hi = build_handle_index(data["primitives"])
    shapes = build_entity_shapes(data["primitives"], hi)
    return hi, shapes


def _resolve_file(file_id: str, require_ready: bool = True) -> FileRecord:
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    if require_ready and rec.status not in (READY, "checking_rules", "report"):
        raise HTTPException(status_code=425, detail=f"file not ready (status={rec.status})")
    return rec


def _shapes_for(file_id: str) -> tuple[dict[str, list[int]], dict[str, EntityShape]]:
    pp = parsed_path(file_id)
    if not pp.exists():
        raise HTTPException(status_code=500, detail="parsed file missing on disk")
    return _cached_shapes(str(pp), pp.stat().st_mtime_ns)


def _parsed_for(file_id: str) -> dict:
    pp = parsed_path(file_id)
    if not pp.exists():
        raise HTTPException(status_code=500, detail="parsed file missing on disk")
    # Guard at the wrapper level (outside `_cached_parsed`'s lru_cache) so a
    # corrupt file yields a contextual 400, not an opaque 500.
    try:
        return _cached_parsed(str(pp), pp.stat().st_mtime_ns)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"parsed file is unreadable/corrupt: {pp}: {exc}",
        )


# ---- Startup / shutdown --------------------------------------------------
def _file_id_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _file_id_from_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _ensure_test_dxf_registered() -> None:
    """First-run convenience: pull `data/test.dxf` into a Sample product."""
    if not TEST_DXF.exists():
        return
    fid = _file_id_from_path(TEST_DXF)
    # Find or create the auto-Sample product.
    sample = next((p for p in PRODUCT_STORE.list_all() if p.name == "Sample"), None)
    if sample is None:
        sample = PRODUCT_STORE.create("Sample", DEFAULT_LIBRARY_ID)
    rec = FILE_STORE.get(fid)
    if rec is None:
        FILE_STORE.register(
            fid, "test.dxf", TEST_DXF.stat().st_size,
            library_id=sample.library_id,
            product_id=sample.id,
            dxf_role="BD",
        )
        rec = FILE_STORE.get(fid)
    dst = upload_path(fid)
    if not dst.exists():
        shutil.copy2(TEST_DXF, dst)
    # If the parsed cache + prematch are already good, leave it alone.
    if (rec is not None
            and rec.status == READY
            and parsed_path(fid).exists()
            and prematch_path(fid).exists()):
        return
    # Otherwise restart from Phase 1 — discover layers, wait for user.
    FILE_STORE.update_status(fid, DISCOVERING_LAYERS)
    jobs.submit_discover_layers(fid)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ensure_test_dxf_registered()
    _submit_unit_rescale_migration()
    yield
    jobs.shutdown()
    from app.matching import shutdown_pool
    shutdown_pool()


logger = logging.getLogger(__name__)


def _submit_unit_rescale_migration() -> None:
    """One-shot: re-preprocess any legacy file whose persisted INSUNITS +
    bbox would now resolve to a non-`1.0` factor under the
    `auto-normalize-unit-suspect-dxf` detector. Idempotent because a
    file that was already rescaled has its bbox stored in mm, so the
    detector returns `1.0` for it the next time around.

    Match JSON invalidation rides along the standard per-file
    re-preprocess flow in `app.jobs._on_reprocess_step_done` (which
    delegates to `_invalidate_match_after_rescale` when the factor
    changes), so no separate cleanup pass is needed here."""
    import math
    from app.dxf import detect_scale_factor

    targets: set[str] = set()
    chosen: dict[str, float] = {}
    for rec in FILE_STORE.list_all():
        if rec.applied_scale != 1.0:
            continue
        # An explicit operator override has authority over the unit; the
        # auto-rescale migration must never re-evaluate or overwrite it
        # (otherwise an "override to mm on a unit-suspect file" — which sits at
        # applied_scale == 1.0 — would be re-queued and re-rescaled every boot).
        if rec.user_unit_override is not None:
            continue
        if rec.bbox is None:
            continue
        xmin, ymin, xmax, ymax = rec.bbox
        diag = math.hypot(max(xmax - xmin, 0.0), max(ymax - ymin, 0.0))
        factor = detect_scale_factor(rec.insunits, diag)
        if factor == 1.0:
            continue
        targets.add(rec.id)
        chosen[rec.id] = factor
    if not targets:
        return
    for fid, factor in chosen.items():
        logger.info(
            "unit-rescale migration: queueing file_id=%s (factor=%.6g)", fid, factor,
        )
    jobs.submit_reprocess_all(file_id_filter=targets, kind="unit-rescale-migration")


app = FastAPI(title="SMDR2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ---- Pages --------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/viewer/{file_id}", response_class=HTMLResponse)
async def viewer(request: Request, file_id: str) -> HTMLResponse:
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    return templates.TemplateResponse(
        request, "viewer.html", {"file_id": file_id, "file_name": rec.name}
    )


# ---- Product API ---------------------------------------------------------
class CreateProductRequest(BaseModel):
    name: str
    library_id: str = DEFAULT_LIBRARY_ID


def _group_files_by_role(files: list[FileRecord]) -> tuple[dict, dict]:
    """Build (`files_by_role`, `files_by_role_all`) from a product's file list.

    `files_by_role[role]` is the primary file dict (the `multi` row if any,
    else the first split row, else None) — preserved for the existing
    rule-results modal and other callers that expect at-most-one file per
    role.

    `files_by_role_all[role]` is the complete list of file dicts (possibly
    empty), ordered with the `multi` row first and split rows after in a
    stable view order.
    """
    view_order = {"multi": 0, "top": 1, "bottom": 2, "side": 3, None: 4}
    by_role_all: dict[str, list[dict]] = {role: [] for role in VALID_ROLES}
    by_role_recs: dict[str, list[FileRecord]] = {role: [] for role in VALID_ROLES}
    for f in files:
        if f.dxf_role in by_role_all:
            by_role_recs[f.dxf_role].append(f)
    for role, recs in by_role_recs.items():
        recs.sort(key=lambda r: view_order.get(r.dxf_view, 9))
        by_role_all[role] = [r.to_dict() for r in recs]
    by_role = {
        role: (lst[0] if lst else None)
        for role, lst in by_role_all.items()
    }
    return by_role, by_role_all


def _project_rule_check_job(job: dict | None) -> dict | None:
    """Trim a `_jobs` entry to the fields the dashboard needs. Returns
    `None` when no job is recorded so the frontend can branch cleanly."""
    if job is None:
        return None
    return {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "submitted_at": job.get("submitted_at"),
        "completed_at": job.get("completed_at"),
        "error": job.get("error"),
        "result": job.get("result"),
    }


@app.get("/api/products")
async def list_products() -> dict:
    """Every product with its files (per role) and rule-check readiness."""
    items = []
    for p in PRODUCT_STORE.list_all():
        files = FILE_STORE.list_by_product(p.id)
        by_role, by_role_all = _group_files_by_role(files)
        uploaded = [f for f in files if f.dxf_role is not None]
        ready_for_rc = bool(uploaded) and all(f.match_saved for f in uploaded)
        items.append({
            **p.to_dict(),
            "files_by_role": by_role,
            "files_by_role_all": by_role_all,
            "match_progress": {
                "saved": sum(1 for f in uploaded if f.match_saved),
                "total": len(uploaded),
            },
            "ready_for_rule_check": ready_for_rc,
            "rule_check_available": rule_check_path(p.id).exists(),
            "latest_rule_check_job": _project_rule_check_job(
                jobs.latest_rule_check_job(p.id)
            ),
        })
    return {"products": items}


@app.get("/api/products/{product_id}")
async def get_product(product_id: str) -> dict:
    p = PRODUCT_STORE.get(product_id)
    if p is None:
        raise HTTPException(status_code=404, detail="product not found")
    files = FILE_STORE.list_by_product(product_id)
    by_role, by_role_all = _group_files_by_role(files)
    uploaded = [f for f in files if f.dxf_role is not None]
    return {
        **p.to_dict(),
        "files_by_role": by_role,
        "files_by_role_all": by_role_all,
        "match_progress": {
            "saved": sum(1 for f in uploaded if f.match_saved),
            "total": len(uploaded),
        },
        "ready_for_rule_check": bool(uploaded) and all(f.match_saved for f in uploaded),
        "rule_check_available": rule_check_path(product_id).exists(),
        "latest_rule_check_job": _project_rule_check_job(
            jobs.latest_rule_check_job(product_id)
        ),
    }


@app.post("/api/products")
async def create_product(req: CreateProductRequest) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not LIBRARIES.exists(req.library_id):
        raise HTTPException(status_code=400, detail=f"unknown library {req.library_id!r}")
    p = PRODUCT_STORE.create(name, req.library_id)
    return p.to_dict()


@app.delete("/api/products/{product_id}")
async def delete_product(product_id: str) -> dict:
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    PRODUCT_STORE.delete(product_id)
    return {"deleted": product_id}


@app.delete("/api/products/{product_id}/files/{file_id}", status_code=204)
async def delete_product_file(product_id: str, file_id: str) -> Response:
    """Detach a file from a product slot. Useful for removing a single
    split-view file from a (role, view) without uploading a replacement.
    The file row itself stays (so other libraries / products that may
    reference it keep working); only the product/role/view binding clears.
    """
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    rec = FILE_STORE.get(file_id)
    if rec is None or rec.product_id != product_id:
        raise HTTPException(status_code=404, detail="file not found in this product")
    with FILE_STORE.lock, FILE_STORE.conn:
        FILE_STORE.conn.execute(
            "UPDATE files SET product_id = NULL, dxf_role = NULL, "
            "dxf_view = NULL, match_saved = 0 WHERE id = ?",
            (file_id,),
        )
    # Drop any cached match JSON so it doesn't haunt a future product binding.
    try:
        match_path(file_id).unlink()
    except FileNotFoundError:
        pass
    return Response(status_code=204)


@app.post("/api/products/{product_id}/files")
async def upload_product_file(
    product_id: str,
    file: UploadFile = File(...),
    dxf_role: str = Form(...),
    replace_file_id: str | None = Form(None),
    skip_layer_pick: bool = Form(False),
) -> dict:
    """Upload a DXF into a product role.

    A `(product, role)` can hold any number of DXFs; this endpoint is
    purely additive by default. To replace an existing file (the
    common "swap this DXF" flow), pass `replace_file_id` — that file
    is detached from the product before the new one is registered.
    View coverage (top/bottom/side) is determined by the per-file
    region rects set later via the side-regions endpoint; the upload
    itself does not assign a view.

    `skip_layer_pick` is a dev-mode shortcut: when true the file
    bypasses Phase 1 entirely (no layer manifest, no per-layer SVG
    thumbnails, no `awaiting_layers` wait) and goes straight to
    Phase 2 with `selected_layers=None` (keep every layer). The flag
    is honoured unconditionally on the server — gating it as
    "dev only" is the dashboard's responsibility, consistent with
    the rest of the dev-overrides surface.
    """
    product = PRODUCT_STORE.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    if dxf_role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {VALID_ROLES}")
    if not file.filename or not file.filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400, detail="expected a .dxf file")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    # Per-file size guard on the buffered body. In multipart/form-data the
    # request Content-Length covers the whole body (fields + boundaries), not
    # the single file part, and FastAPI has already buffered the part by the
    # time we get here — so len(content) is the authoritative per-file check.
    # Bounds accidental huge uploads (wrong/corrupt export); env-tunable.
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit "
                f"(SMDR2_MAX_UPLOAD_MB); got {len(content) // (1024 * 1024)} MB"
            ),
        )

    # Optional targeted eviction of a specific file in this product+role.
    if replace_file_id:
        evictee = FILE_STORE.get(replace_file_id)
        if (
            evictee is None
            or evictee.product_id != product_id
            or evictee.dxf_role != dxf_role
        ):
            raise HTTPException(
                status_code=400,
                detail="replace_file_id does not match a file in this product+role",
            )
        with FILE_STORE.lock, FILE_STORE.conn:
            FILE_STORE.conn.execute(
                "UPDATE files SET product_id = NULL, dxf_role = NULL, "
                "dxf_view = NULL, match_saved = 0 WHERE id = ?",
                (replace_file_id,),
            )
        try:
            match_path(replace_file_id).unlink()
        except FileNotFoundError:
            pass

    fid = _file_id_from_bytes(content)
    dst = upload_path(fid)
    if not dst.exists():
        dst.write_bytes(content)
    initial_status = PREPROCESSING if skip_layer_pick else DISCOVERING_LAYERS
    existing = FILE_STORE.get(fid)
    if existing is None:
        FILE_STORE.register(
            fid, file.filename, len(content),
            library_id=product.library_id,
            product_id=product_id, dxf_role=dxf_role, dxf_view="multi",
            initial_status=initial_status,
        )
    else:
        # Same content hash → reuse the row, rebinding it to this product
        # slot. Wiping selected_layers forces Phase 1 (or, on the skip
        # path, Phase 2) to re-run with a fresh layer set.
        with FILE_STORE.lock, FILE_STORE.conn:
            FILE_STORE.conn.execute(
                "UPDATE files SET product_id = ?, dxf_role = ?, dxf_view = 'multi', "
                "library_id = ?, status = ?, match_saved = 0, "
                "selected_layers = NULL WHERE id = ?",
                (product_id, dxf_role, product.library_id,
                 initial_status, fid),
            )
    if skip_layer_pick:
        # Dev path: no manifest rendering, no `awaiting_layers` wait.
        # `selected_layers=None` is the worker's existing "keep every
        # primitive" signal — no new code path inside the worker.
        job_id = jobs.submit_preprocess(
            fid, library_id=product.library_id, selected_layers=None,
            product_id=product.id,
        )
    else:
        job_id = jobs.submit_discover_layers(fid)
    return {
        "file_id": fid,
        "product_id": product_id,
        "dxf_role": dxf_role,
        "library_id": product.library_id,
        "status": initial_status,
        "job_id": job_id,
    }


class FilePatchRequest(BaseModel):
    library_id: str


class UnitOverrideRequest(BaseModel):
    # One of 'mm' | 'cm' | 'm' | 'inch' | 'μm'. Validated against the
    # canonical map in `app.dxf.UNIT_TO_SCALE` inside the handler.
    unit: str


class RectModel(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class SideRegionsRequest(BaseModel):
    # All three fields are always sent; null means "clear that view". The
    # frontend mirrors local state so a partial update sends the current
    # rect for any untouched view.
    top_view_rect: RectModel | None = None
    bottom_view_rect: RectModel | None = None
    side_view_rect: RectModel | None = None


@app.patch("/api/files/{file_id}")
async def patch_file(file_id: str, req: FilePatchRequest) -> dict:
    """Reassign a file to a different library. Triggers re-preprocessing
    so the pre-match overlay reflects the new library's templates. The
    user's prior `selected_layers` (if any) is reused — no re-prompt."""
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    if not LIBRARIES.exists(req.library_id):
        raise HTTPException(status_code=400, detail=f"unknown library {req.library_id!r}")
    if rec.library_id == req.library_id:
        return {"file_id": file_id, "library_id": req.library_id, "unchanged": True}
    FILE_STORE.update_library(file_id, req.library_id)
    FILE_STORE.update_status(file_id, PREPROCESSING)
    job_id = jobs.submit_preprocess(
        file_id,
        library_id=req.library_id,
        selected_layers=rec.selected_layers,
        product_id=rec.product_id,
    )
    return {"file_id": file_id, "library_id": req.library_id, "job_id": job_id}


@app.post("/api/files/{file_id}/unit-override")
async def post_unit_override(file_id: str, req: UnitOverrideRequest) -> JSONResponse:
    """Apply an operator-chosen unit interpretation to a file. Enqueues
    a background preprocess that re-runs with the new multiplier; the
    response carries the job id and the list of products whose Match
    JSON the recompute will invalidate (so the viewer can show the
    confirm modal's "N products affected" line).

    Returns 202 on success, 400 on bad `unit`, 404 on missing file,
    409 when a preprocess for this file is already in flight."""
    from app.dxf import UNIT_TO_SCALE

    if req.unit not in UNIT_TO_SCALE:
        raise HTTPException(
            status_code=400,
            detail=f"unknown unit {req.unit!r}; expected one of {sorted(UNIT_TO_SCALE)}",
        )
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    inflight = jobs.find_inflight_preprocess_job(file_id)
    if inflight is not None:
        return JSONResponse(
            status_code=409,
            content={"detail": "preprocess in flight", "job_id": inflight},
        )
    job_id = jobs.submit_unit_override_preprocess(file_id, req.unit)
    affected = _affected_products_for_file(rec)
    return JSONResponse(
        status_code=202,
        content={
            "file_id": file_id,
            "unit": req.unit,
            "job_id": job_id,
            "affected_products": affected,
        },
    )


def _affected_products_for_file(rec: FileRecord) -> list[dict]:
    """Return the products whose Match JSON will be cleared when this
    file's `applied_scale` changes. A file row carries at most one
    `product_id`, so the list has 0 or 1 entry today; shaping it as a
    list keeps the API forward-compatible if file→product becomes
    many-to-many."""
    if not rec.product_id:
        return []
    product = PRODUCT_STORE.get(rec.product_id)
    if product is None:
        return []
    return [{"id": product.id, "name": product.name}]


@app.patch("/api/files/{file_id}/side-regions")
async def patch_side_regions(file_id: str, req: SideRegionsRequest) -> dict:
    """Persist this file's top_view / bottom_view / side_view rectangles.

    Invalidates `data/match/{file_id}.json` (rule-checker input)
    because the saved match keys are no longer in sync with the new
    view labels. Resets `match_saved` so the engineer re-runs Save Match
    after redrawing regions.
    """
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    top = normalise_rect(req.top_view_rect.model_dump()) if req.top_view_rect else None
    bottom = normalise_rect(req.bottom_view_rect.model_dump()) if req.bottom_view_rect else None
    side = normalise_rect(req.side_view_rect.model_dump()) if req.side_view_rect else None
    # Each file's region rects are independent — when a (product, role)
    # has multiple DXFs, every file may legitimately mark its own top /
    # bottom / side rectangles. No cross-file uniqueness check here.
    FILE_STORE.update_side_regions(file_id, top, bottom, side)

    # Invalidate the saved match JSON — its keys are stale w.r.t. the new
    # rectangles. The user has to re-run Save Match to regenerate.
    mp = match_path(file_id)
    try:
        mp.unlink()
    except FileNotFoundError:
        pass
    FILE_STORE.set_match_saved(file_id, False)

    return {
        "file_id": file_id,
        "top_view_rect": top,
        "bottom_view_rect": bottom,
        "side_view_rect": side,
        "match_saved": False,
    }


@app.get("/api/files")
async def list_files() -> dict:
    return {"files": [r.to_dict() for r in FILE_STORE.list_all()]}


@app.get("/api/files/{file_id}")
async def get_file(file_id: str) -> dict:
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    return rec.to_dict()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ---- Layer selection (Phase 1 -> Phase 2 gate) --------------------------
def _read_layer_manifest(file_id: str) -> dict | None:
    mp = layer_manifest_path(file_id)
    if not mp.exists():
        return None
    with open(mp) as f:
        return json.load(f)


@app.get("/api/files/{file_id}/layers")
async def get_file_layers(file_id: str) -> dict:
    """Manifest + current selection for a file. 404 if Phase 1 hasn't
    finished yet (no manifest on disk)."""
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    manifest = _read_layer_manifest(file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layer manifest not available")
    return {
        "file_id": file_id,
        "manifest": manifest,
        "selected_layers": rec.selected_layers,
        "status": rec.status,
    }


class LayersConfirmRequest(BaseModel):
    layers: list[str]


@app.post("/api/files/{file_id}/layers")
async def confirm_layers(file_id: str, req: LayersConfirmRequest) -> dict:
    """Persist the user's chosen layer subset and kick off Phase 2."""
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    if not req.layers:
        raise HTTPException(status_code=400, detail="at least one layer required")
    manifest = _read_layer_manifest(file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layer manifest not available")
    known = {layer["name"] for layer in manifest["layers"]}
    unknown = [name for name in req.layers if name not in known]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown layers for this file: {unknown[:5]}",
        )
    # Dedupe + preserve manifest order so the persisted list is stable.
    chosen_set = set(req.layers)
    ordered = [layer["name"] for layer in manifest["layers"] if layer["name"] in chosen_set]
    FILE_STORE.update_selected_layers(file_id, ordered)
    FILE_STORE.update_status(file_id, PREPROCESSING)
    job_id = jobs.submit_preprocess(
        file_id,
        library_id=rec.library_id,
        selected_layers=ordered,
        product_id=rec.product_id,
    )
    return {
        "file_id": file_id,
        "selected_layers": ordered,
        "status": PREPROCESSING,
        "job_id": job_id,
    }


@app.get("/api/files/{file_id}/layer-preview/{safe_name}.svg")
async def get_layer_preview_svg(file_id: str, safe_name: str):
    """Serve one layer's SVG thumbnail. 404 when Phase 1 hasn't completed
    or the requested layer isn't in the file's manifest."""
    from fastapi.responses import FileResponse
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    manifest = _read_layer_manifest(file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layer manifest not available")
    valid = {layer["safe_name"] for layer in manifest["layers"]}
    if safe_name not in valid:
        raise HTTPException(status_code=404, detail="unknown layer for this file")
    path = layer_preview_svg_path(file_id, safe_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="preview SVG missing on disk")
    return FileResponse(path, media_type="image/svg+xml")


@app.post("/api/files/{file_id}/discover-layers")
async def trigger_discover_layers(file_id: str) -> dict:
    """Re-run Phase 1 for a legacy or library-swapped file (e.g. user
    clicked 'Edit layers' on a ready file that pre-dates the feature)."""
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    FILE_STORE.update_status(file_id, DISCOVERING_LAYERS)
    job_id = jobs.submit_discover_layers(file_id)
    return {"file_id": file_id, "status": DISCOVERING_LAYERS, "job_id": job_id}


# ---- Library CRUD -------------------------------------------------------
@app.get("/api/libraries")
async def list_libraries() -> dict:
    return {"libraries": LIBRARIES.list_summaries(), "default_id": DEFAULT_LIBRARY_ID}


class CreateLibraryRequest(BaseModel):
    name: str


@app.post("/api/libraries")
async def create_library(req: CreateLibraryRequest) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    lib = LIBRARIES.create(name)
    return {"id": lib.library_id, "name": name}


@app.delete("/api/libraries/{library_id}")
async def delete_library(library_id: str) -> dict:
    if library_id == DEFAULT_LIBRARY_ID:
        raise HTTPException(status_code=400, detail="cannot delete the default library")
    if not LIBRARIES.exists(library_id):
        raise HTTPException(status_code=404, detail="library not found")
    LIBRARIES.delete(library_id)
    return {"deleted": library_id}


def _resolve_library_id(library_id: str | None, file_id: str | None) -> str:
    """Resolve a library_id from explicit arg or file context, falling back to default."""
    if library_id:
        if not LIBRARIES.exists(library_id):
            raise HTTPException(status_code=404, detail="library not found")
        return library_id
    if file_id:
        rec = FILE_STORE.get(file_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="file not found")
        return rec.library_id
    return DEFAULT_LIBRARY_ID


# ---- Classes / templates within a library -------------------------------
@app.get("/api/libraries/{library_id}/classes")
async def classes_by_library(library_id: str) -> dict:
    if not LIBRARIES.exists(library_id):
        raise HTTPException(status_code=404, detail="library not found")
    return {"library_id": library_id, "classes": LIBRARIES.get(library_id).summary()}


# Default `bbox_ratio` applied when the client sets a class to signature
# mode without naming a value. Mirrors `matching.SIGNATURE_DEFAULT_BBOX_RATIO`
# so the chosen number stays consistent between matcher and API surface.
SIGNATURE_DEFAULT_BBOX_RATIO = 0.05


class ClassStrategyRequest(BaseModel):
    strategy: str
    bbox_ratio: float | None = None


@app.put("/api/libraries/{library_id}/classes/{class_name}/strategy")
async def set_class_strategy(
    library_id: str, class_name: str, req: ClassStrategyRequest,
) -> dict:
    if not LIBRARIES.exists(library_id):
        raise HTTPException(status_code=404, detail="library not found")
    if req.strategy not in ("chamfer", "signature"):
        raise HTTPException(
            status_code=400,
            detail="strategy must be 'chamfer' or 'signature'",
        )
    if req.strategy == "chamfer":
        # bbox_ratio is meaningless under chamfer; always clear it.
        bbox_ratio: float | None = None
    else:
        if req.bbox_ratio is None:
            bbox_ratio = SIGNATURE_DEFAULT_BBOX_RATIO
        else:
            v = req.bbox_ratio
            if not math.isfinite(v) or v <= 0 or v > 1:
                raise HTTPException(
                    status_code=400,
                    detail="bbox_ratio must be in (0, 1] or null",
                )
            bbox_ratio = v
    lib = LIBRARIES.get(library_id)
    if not lib.set_strategy(class_name, req.strategy, bbox_ratio):
        raise HTTPException(status_code=404, detail="class not found")
    return {
        "library_id": library_id,
        "class_name": class_name,
        "match_strategy": req.strategy,
        "bbox_ratio": bbox_ratio,
    }


@app.get("/api/classes")
async def classes_default(file_id: str | None = None,
                          library_id: str | None = None) -> dict:
    """Backwards-compat: returns classes for `library_id` (or the file's
    library if `file_id` given, or the default library otherwise)."""
    lib_id = _resolve_library_id(library_id, file_id)
    return {"library_id": lib_id, "classes": LIBRARIES.get(lib_id).summary()}


@app.get("/api/libraries/{library_id}/templates")
async def list_templates_for_library(library_id: str) -> dict:
    """Library-admin view: returns ONLY library-scoped templates
    (product_id IS NULL). Product-scoped templates (Substrate, Lid,
    DieArea, Ball, Protrusion) are intentionally invisible here — they
    belong to a specific product, not the library. The cached
    `Library` instance is hydrated by `Store.load_library(library_id)`
    with no product_id, so its `all_templates()` cache only ever
    contains library-scoped rows."""
    if not LIBRARIES.exists(library_id):
        raise HTTPException(status_code=404, detail="library not found")
    lib = LIBRARIES.get(library_id)
    items = []
    for cls_name, idx, t in lib.all_templates():
        items.append({
            "id": t.id,
            "library_id": library_id,
            "class_name": cls_name,
            "index": idx,
            "key": f"{cls_name}.{idx}",
            "entity_count": len(t.entity_point_sets),
            "vertex_count": sum(len(e) for e in t.entity_point_sets),
            "bbox": list(t.bbox),
            "centroid": list(t.centroid),
            "entity_point_sets": t.entity_point_sets,
        })
    return {"templates": items, "library_id": library_id}


@app.get("/api/templates")
async def list_templates(library_id: str | None = None, file_id: str | None = None) -> dict:
    """Backwards-compat alias for the library-scoped templates endpoint."""
    lib_id = _resolve_library_id(library_id, file_id)
    return await list_templates_for_library(lib_id)


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: str) -> dict:
    # Search across all libraries — template ids are globally unique (UUIDs).
    for lib_summary in LIBRARIES.list_summaries():
        lib = LIBRARIES.get(lib_summary["id"])
        if lib.delete_template(template_id):
            return {"deleted": template_id, "library_id": lib.library_id}
    raise HTTPException(status_code=404, detail="template not found")


class MoveTemplateRequest(BaseModel):
    class_name: str


@app.patch("/api/templates/{template_id}")
async def patch_template(template_id: str, req: MoveTemplateRequest) -> dict:
    if not req.class_name:
        raise HTTPException(status_code=400, detail="class_name required")
    for lib_summary in LIBRARIES.list_summaries():
        lib = LIBRARIES.get(lib_summary["id"])
        if lib.find_template(template_id) is not None:
            if req.class_name not in {c["name"] for c in lib.summary()}:
                lib.add_class(req.class_name)
            lib.move_template(template_id, req.class_name)
            return {"id": template_id, "class_name": req.class_name,
                    "library_id": lib.library_id}
    raise HTTPException(status_code=404, detail="template not found")


# ---- Per-file: primitives / match / commit / scan-all -------------------
@app.get("/api/files/{file_id}/primitives")
async def primitives(file_id: str) -> dict:
    _resolve_file(file_id)
    data = _parsed_for(file_id)
    return {
        "primitives": data["primitives"],
        "bbox": data["bbox"],
        "background": data["background"],
        "count": len(data["primitives"]),
    }


# Sync (not async) so FastAPI dispatches to the worker thread pool — the
# ~1–2 s `build_entity_shapes` on a 25k-entity drawing must NOT block the
# async event loop. Viewer fires this fire-and-forget after `/primitives`
# returns so the user's first /match scan finds the LRU cache populated.
@app.post("/api/files/{file_id}/warm-shapes")
def warm_shapes(file_id: str) -> dict:
    _resolve_file(file_id)
    _, shapes = _shapes_for(file_id)
    return {"entity_count": len(shapes)}


@app.get("/api/files/{file_id}/debug-radius-buckets")
async def debug_radius_buckets(file_id: str) -> dict:
    """Diagnostic dump for the single-CIRCLE radius-bucket fast path.

    Compares each library/product template's recomputed-via-from_points
    radius with the drawing's analytical-radius bucket distribution.
    Used to chase scan-all-misses-everything bugs that don't reproduce
    on small fixtures."""
    from app.matching import (
        _get_radius_buckets,
        _radius_bucket_key,
        EntityShape,
    )
    rec = _resolve_file(file_id)
    _, shapes = _shapes_for(file_id)
    buckets = _get_radius_buckets(shapes)

    drawing_circle_count = sum(1 for s in shapes.values() if s.kind == "circle")
    other_kind_counts: dict[str, int] = {}
    sample_drawing_circles: list[dict] = []
    for h, s in shapes.items():
        if s.kind != "circle":
            other_kind_counts[s.kind or "_None"] = (
                other_kind_counts.get(s.kind or "_None", 0) + 1
            )
        elif len(sample_drawing_circles) < 5:
            sample_drawing_circles.append({
                "handle": h,
                "radius": float(s.radius),
                "bucket_key": _radius_bucket_key(s.radius),
            })

    classes, _, templates_by_class = LIBRARIES.store.load_library(
        rec.library_id, product_id=rec.product_id,
    )

    per_class: list[dict] = []
    for cls in classes:
        tmpls = templates_by_class.get(cls, [])
        if not tmpls:
            continue
        entry: dict = {"class": cls, "template_count": len(tmpls), "templates": []}
        for tmpl in tmpls[:3]:
            pts = tmpl.entity_point_sets[0] if tmpl.entity_point_sets else []
            kind = (tmpl.entity_kinds or [None])[0]
            tpl = EntityShape.from_points("_t", pts, kind=kind)
            key = _radius_bucket_key(tpl.radius)
            entry["templates"].append({
                "kind": tpl.kind,
                "radius": float(tpl.radius),
                "bucket_key": key,
                "neighbor_hits": {
                    str(k): len(buckets.get(k, []))
                    for k in range(key - 3, key + 4)
                },
                "point_count": len(pts),
                "first_point": list(pts[0]) if pts else None,
            })
        if entry["templates"]:
            per_class.append(entry)

    return {
        "file_id": file_id,
        "library_id": rec.library_id,
        "product_id": rec.product_id,
        "drawing_circle_count": drawing_circle_count,
        "drawing_non_circle_counts": other_kind_counts,
        "drawing_bucket_count": len(buckets),
        "sample_drawing_circles": sample_drawing_circles,
        "per_class": per_class,
    }


class MatchRequest(BaseModel):
    handles: list[str]
    # Optional class hint for add-mode preview: the viewer sends the active
    # add-mode class so the live scan uses the same (strategy, bbox_ratio)
    # that scan-all will use after commit.
    class_name: str | None = None


@app.post("/api/files/{file_id}/match")
async def match(file_id: str, req: MatchRequest) -> dict:
    if not req.handles:
        raise HTTPException(status_code=400, detail="empty template")
    rec = _resolve_file(file_id)
    _, shapes = _shapes_for(file_id)
    missing = [h for h in req.handles if h not in shapes]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown handles: {missing[:5]}")
    strategy = "chamfer"
    bbox_ratio: float | None = None
    if req.class_name:
        strategy, bbox_ratio = LIBRARIES.get(rec.library_id).strategy_of(req.class_name)
    out = find_matches(
        req.handles, shapes,
        strategy=strategy, bbox_ratio=bbox_ratio,
    )
    return {
        "matches": [{"handles": r.handles, "score": r.score, "scale": r.scale} for r in out.matches],
        "near_misses": [
            {"handles": n.handles, "score": n.score, "scale": n.scale, "reason": n.reason}
            for n in out.near_misses
        ],
        "count": len(out.matches),
        "near_count": len(out.near_misses),
    }


class MatchSwapRequest(BaseModel):
    pattern_a: list[str]
    pattern_b: list[str]
    class_name: str | None = None


@app.post("/api/files/{file_id}/match-swap")
async def match_swap(file_id: str, req: MatchSwapRequest) -> dict:
    """Diagnostic: run find_matches with pattern_a as template AND with
    pattern_b as template, and dump per-pair gate + alignment breakdown so an
    asymmetric "A finds B but B doesn't find A" outcome can be pinpointed.

    Curl from the viewer when the bug shows up — body is two handle lists.
    """
    if not req.pattern_a or not req.pattern_b:
        raise HTTPException(
            status_code=400, detail="both pattern_a and pattern_b required",
        )
    rec = _resolve_file(file_id)
    _, shapes = _shapes_for(file_id)
    missing = [h for h in req.pattern_a + req.pattern_b if h not in shapes]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"unknown handles: {missing[:5]}",
        )
    strategy = "chamfer"
    bbox_ratio: float | None = None
    if req.class_name:
        strategy, bbox_ratio = LIBRARIES.get(rec.library_id).strategy_of(
            req.class_name,
        )
    return diagnose_swap(
        req.pattern_a, req.pattern_b, shapes,
        strategy=strategy, bbox_ratio=bbox_ratio,
    )


class CommitRequest(BaseModel):
    class_name: str
    handles: list[str]


@app.post("/api/files/{file_id}/commit")
async def commit(file_id: str, req: CommitRequest) -> dict:
    if not req.handles:
        raise HTTPException(status_code=400, detail="empty template")
    rec = _resolve_file(file_id)
    # Product-scoped classes need a product to land in. A file with no
    # product binding (legacy uploads, dev fixtures) cannot commit one
    # — fail loud rather than silently land the template at library
    # scope where it would leak across every product.
    if is_product_scoped(req.class_name) and rec.product_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"file is not bound to a product; cannot commit "
                f"product-scoped class {req.class_name!r}"
            ),
        )
    lib = LIBRARIES.get(rec.library_id)
    if req.class_name not in {c["name"] for c in lib.summary()}:
        lib.add_class(req.class_name)
    data = _parsed_for(file_id)
    handle_index, _ = _shapes_for(file_id)
    missing = [h for h in req.handles if h not in handle_index]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown handles: {missing[:5]}")
    entity_point_sets = [
        collect_entity_points(data["primitives"], handle_index, h) for h in req.handles
    ]
    entity_kinds = [
        collect_entity_kinds(data["primitives"], handle_index, h) for h in req.handles
    ]
    tmpl = Template.from_entities(req.class_name, entity_point_sets, entity_kinds)
    stored, already_existed = lib.add_template_for_file(tmpl, product_id=rec.product_id)
    return {
        "template_id": stored.id,
        "class_name": stored.class_name,
        "library_id": rec.library_id,
        "count": lib.count(stored.class_name),
        "already_existed": already_existed,
    }


@app.get("/api/files/{file_id}/scan-all")
async def scan_all(file_id: str) -> dict:
    """Overlay-only preview of "what class does each handle belong to".

    Runs the *same* pipeline as `save_match_json` — view split via
    `split_matches_by_side` — so the overlay's per-class colouring matches
    what Save Match would persist to disk. Same-geometry classes that would
    otherwise cross-fire (FiducialCircle + BGABall sharing a circle radius)
    are disambiguated by their mutually exclusive view constraints (BGABall
    bottom-only, FiducialCircle top-only): a match in a disallowed view is
    dropped by `split_matches_by_side`.

    Response shape: `{by_class: {<display_name>: [handle, ...]}, total: N}`.
    Front-end overlay code (`runScanAll` in `canvas.js`) reads
    `data.by_class[cls]`.
    """
    rec = _resolve_file(file_id)
    # Scope-aware load: merge library-scoped templates with this file's
    # product-scoped templates. Bypass the cached `Library` (LIBRARIES.get)
    # because that cache is hydrated once at library scope only and would
    # not see product-scoped Substrate/Lid/DieArea/Ball templates committed
    # by other files in the same product. The cache is intentionally
    # library-only for admin views (`/api/libraries/{id}/templates`); the
    # matcher reads through this fresh Store call instead.
    if LIBRARIES.get(rec.library_id) is None:
        raise HTTPException(
            status_code=404, detail=f"library {rec.library_id!r} not registered"
        )
    classes, configs_by_class, templates_by_class = LIBRARIES.store.load_library(
        rec.library_id, product_id=rec.product_id
    )
    _, shapes = _shapes_for(file_id)

    # View-rect dispatch (mirror of save_match_json). Used by
    # split_matches_by_side and by the skip-when-impossible gate below.
    rect_for = {
        "top_view": rec.top_view_rect,
        "bottom_view": rec.bottom_view_rect,
        "side_view": rec.side_view_rect,
    }

    # Prefixed-key dict, same shape save_match_json builds before
    # persistence. BGABall/FiducialCircle are disambiguated by the view
    # constraints applied in split_matches_by_side below — no arbitration step.
    out: dict[str, list[list[str]]] = {}

    for cls_name in classes:
        # Skip-when-impossible: class is view-constrained and none of
        # its allowed view rects is set on the file → every match would
        # be dropped by split_matches_by_side anyway. Pure perf gate.
        allowed = CLASS_VIEW_CONSTRAINTS.get(cls_name)
        if allowed is not None and not any(rect_for[v] is not None for v in allowed):
            continue
        cfg = configs_by_class.get(cls_name, {})
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
            grouped, _cnts = split_matches_by_side(
                base_key, result.matches, shapes,
                rec.top_view_rect, rec.bottom_view_rect, rec.side_view_rect,
                class_name=cls_name,
            )
            for k, v in grouped.items():
                out.setdefault(k, []).extend(v)

    # Collapse prefixed-keys back to the flat {display_name: handles}
    # shape the overlay expects. Reverse-map snake_case → display name
    # via CLASS_JSON_KEY (classes without a snake override use the
    # snake name as their display name via .get fallback).
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

    by_class = {k: sorted(v) for k, v in by_class_sets.items()}
    return {
        "by_class": by_class,
        "total": sum(len(v) for v in by_class.values()),
    }


# ---- Pre-match cache (written by preprocess worker) ---------------------
@app.get("/api/files/{file_id}/prematch")
async def prematch(file_id: str) -> dict:
    _resolve_file(file_id)
    pp = prematch_path(file_id)
    if not pp.exists():
        # The worker didn't get to this step (older DB row) — return empty.
        return {"by_class": {}, "total": 0, "stale": True}
    return _load_json_or_http(pp, kind="prematch")


# ---- Match JSON ----------------------------------------------------------
# Format the downstream rule-checker expects:
#   { "<view>.<class_snake>.<template_index>": [[handle, ...], ...], ... }
# Class portion is the snake_case form (substrate, smd_2t, bga_ball, ...)
# from CLASS_JSON_KEY; the view prefix is omitted when no side region
# contains the instance. Each inner list is one match occurrence,
# containing the DXF entity handles that make up that occurrence.
#
# The endpoint is async: it submits a `save_match` job to the worker
# pool and returns 202 + {job_id, file_id} immediately. The result
# (template_keys / total_matches / side_counts / saved_to) lives on
# `GET /api/jobs/{job_id}` under `result` once the
# job reaches status=done; `file.match_saved` flips at that point too.
@app.post("/api/files/{file_id}/match-json")
async def save_match_json(file_id: str) -> JSONResponse:
    rec = _resolve_file(file_id)
    if LIBRARIES.get(rec.library_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"library {rec.library_id!r} not registered",
        )
    if not parsed_path(file_id).exists():
        raise HTTPException(
            status_code=500, detail="parsed file missing on disk",
        )
    job_id = jobs.submit_save_match(file_id)
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "file_id": file_id},
    )


@app.get("/api/files/{file_id}/match-json")
async def get_match_json(file_id: str) -> dict:
    _resolve_file(file_id)
    mp = match_path(file_id)
    if not mp.exists():
        raise HTTPException(status_code=404, detail="match JSON not yet generated")
    return _load_json_or_http(mp, kind="match")


# ---- Rule checking (product-scoped, cross-DXF) --------------------------
@app.post("/api/products/{product_id}/rule-check")
async def run_product_rule_check(product_id: str) -> JSONResponse:
    """Submit a product-scoped DRC job. Returns 202 + `{job_id}`; the
    front-end polls `GET /api/jobs/{job_id}` for completion. Every
    file's `match_saved` must be true; otherwise we 400 with a list of
    missing roles. The worker materialises the DRC handoff bundle on
    disk and hands the directory to the external rule function — see
    `app.jobs._rule_check_worker`."""
    product = PRODUCT_STORE.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    files = [f for f in FILE_STORE.list_by_product(product_id) if f.dxf_role]
    if not files:
        raise HTTPException(status_code=400, detail="no DXFs uploaded to this product yet")
    missing = [f.dxf_role for f in files if not f.match_saved]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"these roles still need Save Match: {', '.join(sorted(missing))}",
        )

    # Validate that every role-attached file's Match JSON and DXF
    # exist on disk before submitting — the worker materialises a
    # handoff bundle from these and would fail late otherwise. No
    # parsed-JSON read happens here; the bundle ships DXF + Match
    # JSON only, no `parsed/{file_id}.json` involvement.
    for f in files:
        mp = match_path(f.id)
        if not mp.exists():
            raise HTTPException(
                status_code=400,
                detail=f"{f.dxf_role}: Match JSON missing at {mp.name}",
            )
        if not upload_path(f.id).exists():
            raise HTTPException(
                status_code=500,
                detail=f"{f.dxf_role}: source DXF missing at {f.id}.dxf",
            )

    job_id = jobs.submit_rule_check(product_id, [f.id for f in files])
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "product_id": product_id},
    )


@app.get("/api/products/{product_id}/rule-check")
async def get_product_rule_check(product_id: str) -> dict:
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    rp = rule_check_path(product_id)
    if not rp.exists():
        raise HTTPException(status_code=404, detail="rule check not yet run")
    result = _load_json_or_http(rp, kind="rule-check")
    # Re-validate the persisted payload against the envelope contract so a
    # structurally-corrupt-but-parseable file surfaces loudly (400) instead of
    # producing silent wrong pass/fail counts. Mirrors upload_product_rule_check.
    from app.rule_check import RuleCheckOutputError, _validate_envelope
    try:
        _validate_envelope(result)
    except RuleCheckOutputError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"rule-check JSON failed envelope validation: {exc}",
        )
    n_pass = sum(1 for v in result.values() if v.get("pass"))
    return {
        "product_id": product_id,
        "results": result,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
    }


@app.post("/api/products/{product_id}/rule-check/upload")
async def upload_product_rule_check(product_id: str, request: Request) -> dict:
    """Dev-only: accept a hand-crafted RuleChecking JSON, validate it
    against the envelope contract, and persist it as if the worker had
    written it. Lets the external team iterate on output shape without
    running their pipeline. Hidden in the UI unless dev mode is on; the
    endpoint itself is always reachable (single-user dev tool)."""
    from app.rule_check import RuleCheckOutputError, _validate_envelope

    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")
    try:
        _validate_envelope(payload)
    except RuleCheckOutputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rp = rule_check_path(product_id)
    rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    n_pass = sum(1 for v in payload.values() if v.get("pass"))
    return {
        "product_id": product_id,
        "saved_to": str(rp),
        "rule_count": len(payload),
        "pass_count": n_pass,
        "fail_count": len(payload) - n_pass,
    }


# ---- DRC handoff bundle (external rule-check team consumes this) --------
@app.get("/api/products/{product_id}/drc-bundle")
async def get_drc_bundle(product_id: str) -> Response:
    """Return a zip bundle of every role-attached DXF + per-file Match
    JSON + a manifest the external rule-checking team consumes. Each
    DXF stays in its own coordinate space; every Match JSON ships with
    raw DXF handles.

    Preconditions match `POST .../rule-check`: 404 on unknown product,
    400 when no role-attached DXFs exist or any file still needs Save
    Match. The bundle format itself is pinned by
    `openspec/specs/design-rule-checking/drc-manifest.schema.json`.
    """
    product = PRODUCT_STORE.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    files = [f for f in FILE_STORE.list_by_product(product_id) if f.dxf_role]
    if not files:
        raise HTTPException(
            status_code=400,
            detail="no DXFs uploaded to this product yet",
        )
    missing = [f.dxf_role for f in files if not f.match_saved]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"these roles still need Save Match: {', '.join(sorted(missing))}",
        )
    zip_bytes, filename = build_bundle(product, files)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---- Developer-mode parameter overrides ---------------------------------
# Process-wide, in-memory only. See `app/dev_overrides.py` and the
# `expose-dev-parameter-overrides` OpenSpec change for the contract.
@app.get("/api/dev/settings")
async def dev_settings_get() -> dict:
    from app import dev_overrides
    return {"settings": dev_overrides.read_state()}


@app.post("/api/dev/settings")
async def dev_settings_post(payload: dict) -> dict:
    from app import dev_overrides
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected JSON object")
    # Reset path takes precedence and ignores any other keys in the body —
    # `{"reset": true}` is the canonical wipe.
    if payload.get("reset") is True:
        return {"settings": dev_overrides.reset()}
    overrides = {k: v for k, v in payload.items() if k != "reset"}
    try:
        state = dev_overrides.apply(overrides)
    except dev_overrides.ValidationError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors})
    return {"settings": state}


@app.post("/api/dev/reprocess-all")
async def dev_reprocess_all() -> dict:
    job_id = jobs.submit_reprocess_all()
    return {"job_id": job_id}
