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
import shutil
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
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
    DEFAULT_LIBRARY_ID,
    LIBRARIES,
    Template,
    build_handle_index,
    collect_entity_points,
)
from app.matching import (
    EntityShape,
    build_entity_shapes,
    find_matches,
    find_matches_from_pointsets,
)
from app.products import PRODUCT_STORE, VALID_ROLES, Product
from app.rule_check import check_rules
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
    yield
    jobs.shutdown()
    from app.matching import shutdown_pool
    shutdown_pool()


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


@app.get("/api/products")
async def list_products() -> dict:
    """Every product with its files (per role) and rule-check readiness."""
    items = []
    for p in PRODUCT_STORE.list_all():
        files = FILE_STORE.list_by_product(p.id)
        by_role = {role: None for role in VALID_ROLES}
        for f in files:
            if f.dxf_role in by_role:
                by_role[f.dxf_role] = f.to_dict()
        uploaded = [f for f in files if f.dxf_role is not None]
        ready_for_rc = bool(uploaded) and all(f.match_saved for f in uploaded)
        items.append({
            **p.to_dict(),
            "files_by_role": by_role,
            "match_progress": {
                "saved": sum(1 for f in uploaded if f.match_saved),
                "total": len(uploaded),
            },
            "ready_for_rule_check": ready_for_rc,
            "rule_check_available": rule_check_path(p.id).exists(),
        })
    return {"products": items}


@app.get("/api/products/{product_id}")
async def get_product(product_id: str) -> dict:
    p = PRODUCT_STORE.get(product_id)
    if p is None:
        raise HTTPException(status_code=404, detail="product not found")
    files = FILE_STORE.list_by_product(product_id)
    by_role = {role: None for role in VALID_ROLES}
    for f in files:
        if f.dxf_role in by_role:
            by_role[f.dxf_role] = f.to_dict()
    uploaded = [f for f in files if f.dxf_role is not None]
    return {
        **p.to_dict(),
        "files_by_role": by_role,
        "match_progress": {
            "saved": sum(1 for f in uploaded if f.match_saved),
            "total": len(uploaded),
        },
        "ready_for_rule_check": bool(uploaded) and all(f.match_saved for f in uploaded),
        "rule_check_available": rule_check_path(product_id).exists(),
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


@app.post("/api/products/{product_id}/files")
async def upload_product_file(
    product_id: str,
    file: UploadFile = File(...),
    dxf_role: str = Form(...),
) -> dict:
    """Upload one DXF into a product slot. Replaces an existing slot if any."""
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

    # If a file already occupies this slot, free it so the unique index allows the new one.
    existing_in_slot = next(
        (f for f in FILE_STORE.list_by_product(product_id) if f.dxf_role == dxf_role),
        None,
    )
    if existing_in_slot is not None:
        # Clear product/role on the old file so it doesn't collide with the new one.
        with FILE_STORE.lock, FILE_STORE.conn:
            FILE_STORE.conn.execute(
                "UPDATE files SET product_id = NULL, dxf_role = NULL WHERE id = ?",
                (existing_in_slot.id,),
            )

    fid = _file_id_from_bytes(content)
    dst = upload_path(fid)
    if not dst.exists():
        dst.write_bytes(content)
    existing = FILE_STORE.get(fid)
    if existing is None:
        FILE_STORE.register(
            fid, file.filename, len(content),
            library_id=product.library_id,
            product_id=product_id, dxf_role=dxf_role,
            initial_status=DISCOVERING_LAYERS,
        )
    else:
        # Re-uploading into an existing slot: bytes may be identical, but
        # treat this as a fresh discovery pass either way — the user may be
        # swapping in a new file and the prior layer selection no longer
        # applies. Wiping selected_layers forces Phase 1 to re-run.
        with FILE_STORE.lock, FILE_STORE.conn:
            FILE_STORE.conn.execute(
                "UPDATE files SET product_id = ?, dxf_role = ?, library_id = ?, "
                "status = ?, match_saved = 0, selected_layers = NULL WHERE id = ?",
                (product_id, dxf_role, product.library_id, DISCOVERING_LAYERS, fid),
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


@app.post("/api/files/{file_id}/match")
async def match(file_id: str, req: MatchRequest) -> dict:
    if not req.handles:
        raise HTTPException(status_code=400, detail="empty template")
    _resolve_file(file_id)
    _, shapes = _shapes_for(file_id)
    missing = [h for h in req.handles if h not in shapes]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown handles: {missing[:5]}")
    out = find_matches(req.handles, shapes)
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
    tmpl = Template.from_entities(req.class_name, entity_point_sets)
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
        seen: set[str] = set()
        for tmpl in lib.templates_of(cls_name):
            out = find_matches_from_pointsets(tmpl.entity_point_sets, shapes)
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
#   { "<className>.<template_index>": [[handle, ...], ...], ... }
# Each inner list is one match (one occurrence of the template), containing
# the DXF entity handles that make up that occurrence.
@app.post("/api/files/{file_id}/match-json")
async def save_match_json(file_id: str) -> dict:
    rec = _resolve_file(file_id)
    lib = LIBRARIES.get(rec.library_id)
    _, shapes = _shapes_for(file_id)
    out: dict[str, list[list[str]]] = {}
    total_matches = 0
    for cls_name in lib.classes:
        for idx, tmpl in enumerate(lib.templates_of(cls_name)):
            result = find_matches_from_pointsets(tmpl.entity_point_sets, shapes)
            key = f"{cls_name}.{idx}"
            out[key] = [list(m.handles) for m in result.matches]
            total_matches += len(result.matches)
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
async def run_product_rule_check(product_id: str) -> dict:
    """Run DRC across every uploaded DXF in the product. Every file's
    `match_saved` must be true; otherwise we 400 with a list of missing
    roles."""
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

    # Build the per-role payload the rule checker consumes.
    dxfs_by_role: dict[str, dict] = {}
    for f in files:
        mp = match_path(f.id)
        if not mp.exists():
            raise HTTPException(
                status_code=400,
                detail=f"{f.dxf_role}: Match JSON missing at {mp.name}",
            )
        with open(mp) as fp:
            mj = json.load(fp)
        _, shapes = _shapes_for(f.id)
        dxfs_by_role[f.dxf_role] = {
            "file_id": f.id,
            "dxf_path": str(upload_path(f.id)),
            "match_json": mj,
            "entity_shapes": shapes,
        }

    result = check_rules(product_id, dxfs_by_role)
    dst = rule_check_path(product_id)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w") as fp:
        json.dump(result, fp, indent=2)
    n_pass = sum(1 for v in result.values() if v.get("pass"))
    return {
        "product_id": product_id,
        "results": result,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
        "saved_to": str(dst.relative_to(DATA_DIR.parent)),
        "roles_covered": sorted(dxfs_by_role.keys()),
    }


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
