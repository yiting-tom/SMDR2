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
from app.class_arbitration import arbitrate
from app.library import (
    CLASS_ARBITRATION_GROUPS,
    CLASS_JSON_KEY,
    CLASS_VIEW_CONSTRAINTS,
    DEFAULT_LIBRARY_ID,
    LIBRARIES,
    Template,
    build_handle_index,
    collect_entity_kinds,
    collect_entity_points,
)
from app.matching import (
    EntityShape,
    build_entity_shapes,
    find_matches,
    find_matches_from_pointsets,
)
from app.products import PRODUCT_STORE, VALID_ROLES, Product
from app.drc_bundle import build_bundle
from app.side_regions import normalise_rect, split_matches_by_side
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
    return _cached_parsed(str(pp), pp.stat().st_mtime_ns)


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
) -> dict:
    """Upload a DXF into a product role.

    A `(product, role)` can hold any number of DXFs; this endpoint is
    purely additive by default. To replace an existing file (the
    common "swap this DXF" flow), pass `replace_file_id` — that file
    is detached from the product before the new one is registered.
    View coverage (top/bottom/side) is determined by the per-file
    region rects set later via the side-regions endpoint; the upload
    itself does not assign a view.
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
    existing = FILE_STORE.get(fid)
    if existing is None:
        FILE_STORE.register(
            fid, file.filename, len(content),
            library_id=product.library_id,
            product_id=product_id, dxf_role=dxf_role, dxf_view="multi",
            initial_status=DISCOVERING_LAYERS,
        )
    else:
        # Same content hash → reuse the row, rebinding it to this product
        # slot. Wiping selected_layers forces Phase 1 to re-run.
        with FILE_STORE.lock, FILE_STORE.conn:
            FILE_STORE.conn.execute(
                "UPDATE files SET product_id = ?, dxf_role = ?, dxf_view = 'multi', "
                "library_id = ?, status = ?, match_saved = 0, "
                "selected_layers = NULL WHERE id = ?",
                (product_id, dxf_role, product.library_id,
                 DISCOVERING_LAYERS, fid),
            )
    job_id = jobs.submit_discover_layers(fid)
    return {
        "file_id": fid,
        "product_id": product_id,
        "dxf_role": dxf_role,
        "library_id": product.library_id,
        "status": DISCOVERING_LAYERS,
        "job_id": job_id,
    }


class FilePatchRequest(BaseModel):
    library_id: str


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
    )
    return {"file_id": file_id, "library_id": req.library_id, "job_id": job_id}


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
    # bottom / side rectangles, and downstream rule-check merges all of
    # them into one role-level bundle. No cross-file uniqueness check
    # here.
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


class CommitRequest(BaseModel):
    class_name: str
    handles: list[str]


@app.post("/api/files/{file_id}/commit")
async def commit(file_id: str, req: CommitRequest) -> dict:
    if not req.handles:
        raise HTTPException(status_code=400, detail="empty template")
    rec = _resolve_file(file_id)
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
    lib.add_template(tmpl)
    return {
        "template_id": tmpl.id,
        "class_name": tmpl.class_name,
        "library_id": rec.library_id,
        "count": lib.count(tmpl.class_name),
    }


@app.get("/api/files/{file_id}/scan-all")
async def scan_all(file_id: str) -> dict:
    rec = _resolve_file(file_id)
    lib = LIBRARIES.get(rec.library_id)
    _, shapes = _shapes_for(file_id)
    by_class: dict[str, list[str]] = {}
    for cls_name in lib.classes:
        strategy, bbox_ratio = lib.strategy_of(cls_name)
        seen: set[str] = set()
        for tmpl in lib.templates_of(cls_name):
            out = find_matches_from_pointsets(
                tmpl.entity_point_sets, shapes,
                entity_kinds=tmpl.entity_kinds,
                strategy=strategy, bbox_ratio=bbox_ratio,
            )
            for m in out.matches:
                for h in m.handles:
                    seen.add(h)
        if seen:
            by_class[cls_name] = sorted(seen)
    return {"by_class": by_class, "total": sum(len(v) for v in by_class.values())}


# ---- Pre-match cache (written by preprocess worker) ---------------------
@app.get("/api/files/{file_id}/prematch")
async def prematch(file_id: str) -> dict:
    _resolve_file(file_id)
    pp = prematch_path(file_id)
    if not pp.exists():
        # The worker didn't get to this step (older DB row) — return empty.
        return {"by_class": {}, "total": 0, "stale": True}
    with open(pp) as f:
        return json.load(f)


# ---- Match JSON ----------------------------------------------------------
# Format the downstream rule-checker expects:
#   { "<view>.<class_snake>.<template_index>": [[handle, ...], ...], ... }
# Class portion is the snake_case form (substrate, smd_2t, bga_ball, ...)
# from CLASS_JSON_KEY; the view prefix is omitted when no side region
# contains the instance. Each inner list is one match occurrence,
# containing the DXF entity handles that make up that occurrence.
@app.post("/api/files/{file_id}/match-json")
async def save_match_json(file_id: str) -> dict:
    rec = _resolve_file(file_id)
    lib = LIBRARIES.get(rec.library_id)
    _, shapes = _shapes_for(file_id)
    out: dict[str, list[list[str]]] = {}
    total_matches = 0
    side_counts = {"top_view": 0, "bottom_view": 0, "side_view": 0,
                   "unassigned": 0, "dropped": 0}
    # Map view name → the file's rect for that view, for the
    # skip-when-impossible guard below.
    rect_for = {
        "top_view": rec.top_view_rect,
        "bottom_view": rec.bottom_view_rect,
        "side_view": rec.side_view_rect,
    }
    for cls_name in lib.classes:
        # Skip-when-impossible: if this class has a view constraint and
        # none of its allowed view rectangles is set on the file, every
        # produced match would be dropped by the filter inside
        # split_matches_by_side. Skipping the matcher call is a pure
        # perf optimisation — the result is byte-identical either way.
        allowed = CLASS_VIEW_CONSTRAINTS.get(cls_name)
        if allowed is not None and not any(rect_for[v] is not None for v in allowed):
            continue
        strategy, bbox_ratio = lib.strategy_of(cls_name)
        for idx, tmpl in enumerate(lib.templates_of(cls_name)):
            result = find_matches_from_pointsets(
                tmpl.entity_point_sets, shapes,
                entity_kinds=tmpl.entity_kinds,
                strategy=strategy, bbox_ratio=bbox_ratio,
            )
            # Display name (Substrate, BGABall, …) stays canonical in the UI;
            # match-JSON keys use the snake_case variant so downstream
            # consumers (rule checker, exports) see e.g. top_view.bga_ball.0.
            json_cls = CLASS_JSON_KEY.get(cls_name, cls_name)
            base_key = f"{json_cls}.{idx}"
            # Each match instance is classified by which view rect it
            # falls inside, producing keys like top_view.smd_2t.0; matches
            # outside every rect collapse to the unprefixed base_key.
            # Constrained-class matches (C4Ball/BGABall) that land in a
            # disallowed view or unassigned are dropped by the filter.
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
    # Post-match arbitration: resolves geometrically-identical class
    # collisions (e.g., BGABall vs FiducialCircle when diameters match)
    # by neighbour count. No-op when no arbitration group's members
    # appear in `out`.
    out, arbitration_counts, view_drops = arbitrate(
        out, shapes, CLASS_ARBITRATION_GROUPS
    )
    # Fold view-conflict drops from arbitration into side_counts so the
    # response stays internally consistent. A drop here means an instance
    # that was originally counted under some prefix (top/bottom/side/
    # unassigned) is now moving to "dropped".
    for _label, by_prefix in view_drops.items():
        for prefix, n in by_prefix.items():
            bucket = prefix if prefix else "unassigned"
            side_counts[bucket] = max(0, side_counts[bucket] - n)
            side_counts["dropped"] += n
    dst = match_path(file_id)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w") as f:
        json.dump(out, f, indent=2)
    # Marks the file as ready for product-level rule checking.
    FILE_STORE.set_match_saved(file_id, True)
    return {
        "file_id": file_id,
        "library_id": rec.library_id,
        "template_keys": list(out.keys()),
        "total_matches": total_matches,
        "side_counts": side_counts,
        "arbitration_counts": arbitration_counts,
        "saved_to": str(dst.relative_to(DATA_DIR.parent)),
        "match_saved": True,
    }


@app.get("/api/files/{file_id}/match-json")
async def get_match_json(file_id: str) -> dict:
    _resolve_file(file_id)
    mp = match_path(file_id)
    if not mp.exists():
        raise HTTPException(status_code=404, detail="match JSON not yet generated")
    with open(mp) as f:
        return json.load(f)


# ---- Rule checking (product-scoped, cross-DXF) --------------------------
@app.post("/api/products/{product_id}/rule-check")
async def run_product_rule_check(product_id: str) -> JSONResponse:
    """Submit a product-scoped DRC job. Returns 202 + `{job_id}`; the
    front-end polls `GET /api/jobs/{job_id}` for completion. Every
    file's `match_saved` must be true; otherwise we 400 with a list of
    missing roles. The actual work (read every role's match JSON, load
    parsed shapes, merge with handle-namespacing, run `check_rules`,
    write `rule_check.json`) runs in a worker process via
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

    # Group files by role and build the lightweight role_specs the
    # worker consumes. Each spec carries file paths only — no JSON is
    # read here, so the event loop never blocks on DRC I/O. The worker
    # reproduces the handle-namespacing rule (`<short_file_id>:` prefix
    # when a role has 2+ files) — see `app/jobs.py:_rule_check_worker`.
    files_by_role_recs: dict[str, list[FileRecord]] = {}
    for f in files:
        files_by_role_recs.setdefault(f.dxf_role, []).append(f)

    role_specs: list[dict] = []
    for role, role_files in files_by_role_recs.items():
        file_ids: list[str] = []
        match_json_paths: list[str] = []
        parsed_paths: list[str] = []
        dxf_paths: list[str] = []
        for f in role_files:
            mp = match_path(f.id)
            if not mp.exists():
                raise HTTPException(
                    status_code=400,
                    detail=f"{f.dxf_role}: Match JSON missing at {mp.name}",
                )
            pp = parsed_path(f.id)
            if not pp.exists():
                raise HTTPException(
                    status_code=500,
                    detail=f"{f.dxf_role}: parsed file missing at {pp.name}",
                )
            file_ids.append(f.id)
            match_json_paths.append(str(mp))
            parsed_paths.append(str(pp))
            dxf_paths.append(str(upload_path(f.id)))
        role_specs.append({
            "role": role,
            "file_ids": file_ids,
            "match_json_paths": match_json_paths,
            "parsed_paths": parsed_paths,
            "dxf_paths": dxf_paths,
            "namespaced": len(role_files) > 1,
        })

    job_id = jobs.submit_rule_check(product_id, role_specs)
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
    with open(rp) as fp:
        result = json.load(fp)
    n_pass = sum(1 for v in result.values() if v.get("pass"))
    return {
        "product_id": product_id,
        "results": result,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
    }


# ---- DRC handoff bundle (external rule-check team consumes this) --------
@app.get("/api/products/{product_id}/drc-bundle")
async def get_drc_bundle(product_id: str) -> Response:
    """Return a zip bundle of every role-attached DXF + per-file Match
    JSON + a manifest the external rule-checking team consumes. The
    Match JSONs are shipped raw (no `<file_id[:8]>:` merge prefix);
    each DXF stays in its own coordinate space.

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
