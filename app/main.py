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
from app.files import FILE_STORE, FileRecord, PREPROCESSING, READY
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
from app.rule_check import check_rules
from app.storage import (
    DATA_DIR,
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
    """First-run convenience: pull `data/test.dxf` into the upload pool."""
    if not TEST_DXF.exists():
        return
    fid = _file_id_from_path(TEST_DXF)
    rec = FILE_STORE.get(fid)
    if rec is None:
        FILE_STORE.register(fid, "test.dxf", TEST_DXF.stat().st_size,
                            library_id=DEFAULT_LIBRARY_ID)
        rec = FILE_STORE.get(fid)
    dst = upload_path(fid)
    if not dst.exists():
        shutil.copy2(TEST_DXF, dst)
    if (rec is None
            or rec.status != READY
            or not parsed_path(fid).exists()
            or not prematch_path(fid).exists()):
        FILE_STORE.update_status(fid, PREPROCESSING)
        jobs.submit_preprocess(fid, library_id=rec.library_id if rec else DEFAULT_LIBRARY_ID)


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


# ---- File API ------------------------------------------------------------
@app.post("/api/files")
async def upload_files(
    files: list[UploadFile] = File(...),
    library_id: str = Form(DEFAULT_LIBRARY_ID),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="no files")
    if not LIBRARIES.exists(library_id):
        raise HTTPException(status_code=400, detail=f"unknown library {library_id!r}")
    results = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".dxf"):
            results.append({"name": f.filename, "skipped": "not a .dxf"})
            continue
        content = await f.read()
        if not content:
            results.append({"name": f.filename, "skipped": "empty"})
            continue
        fid = _file_id_from_bytes(content)
        dst = upload_path(fid)
        if not dst.exists():
            dst.write_bytes(content)
        existing = FILE_STORE.get(fid)
        if existing is None:
            FILE_STORE.register(fid, f.filename, len(content), library_id=library_id)
        elif (existing.status == READY
              and parsed_path(fid).exists()
              and prematch_path(fid).exists()
              and existing.library_id == library_id):
            results.append({"file_id": fid, "name": f.filename, "status": READY,
                            "library_id": library_id, "deduped": True})
            continue
        else:
            # Re-process if library changed or file isn't ready.
            FILE_STORE.update_status(fid, PREPROCESSING)
            if existing and existing.library_id != library_id:
                FILE_STORE.update_library(fid, library_id)
        job_id = jobs.submit_preprocess(fid, library_id=library_id)
        results.append({"file_id": fid, "name": f.filename, "status": PREPROCESSING,
                        "library_id": library_id, "job_id": job_id})
    return {"files": results}


class FilePatchRequest(BaseModel):
    library_id: str


@app.patch("/api/files/{file_id}")
async def patch_file(file_id: str, req: FilePatchRequest) -> dict:
    """Reassign a file to a different library. Triggers re-preprocessing
    so the pre-match overlay reflects the new library's templates."""
    rec = FILE_STORE.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found")
    if not LIBRARIES.exists(req.library_id):
        raise HTTPException(status_code=400, detail=f"unknown library {req.library_id!r}")
    if rec.library_id == req.library_id:
        return {"file_id": file_id, "library_id": req.library_id, "unchanged": True}
    FILE_STORE.update_library(file_id, req.library_id)
    FILE_STORE.update_status(file_id, PREPROCESSING)
    job_id = jobs.submit_preprocess(file_id, library_id=req.library_id)
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
    return {
        "file_id": file_id,
        "library_id": rec.library_id,
        "template_keys": list(out.keys()),
        "total_matches": total_matches,
        "saved_to": str(dst.relative_to(DATA_DIR.parent)),
    }


@app.get("/api/files/{file_id}/match-json")
async def get_match_json(file_id: str) -> dict:
    _resolve_file(file_id)
    mp = match_path(file_id)
    if not mp.exists():
        raise HTTPException(status_code=404, detail="match JSON not yet generated")
    with open(mp) as f:
        return json.load(f)


# ---- Rule checking -------------------------------------------------------
@app.post("/api/files/{file_id}/rule-check")
async def run_rule_check(file_id: str) -> dict:
    """Run DRC against the Match JSON, save and return the result."""
    _resolve_file(file_id)
    mp = match_path(file_id)
    if not mp.exists():
        raise HTTPException(
            status_code=400,
            detail="Match JSON missing — Save Match first.",
        )
    with open(mp) as f:
        match_json = json.load(f)
    _, shapes = _shapes_for(file_id)
    result = check_rules(str(upload_path(file_id)), match_json, entity_shapes=shapes)
    dst = rule_check_path(file_id)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w") as f:
        json.dump(result, f, indent=2)
    n_pass = sum(1 for v in result.values() if v.get("pass"))
    return {
        "results": result,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
        "saved_to": str(dst.relative_to(DATA_DIR.parent)),
    }


@app.get("/api/files/{file_id}/rule-check")
async def get_rule_check(file_id: str) -> dict:
    _resolve_file(file_id)
    rp = rule_check_path(file_id)
    if not rp.exists():
        raise HTTPException(status_code=404, detail="rule check not yet run")
    with open(rp) as f:
        result = json.load(f)
    n_pass = sum(1 for v in result.values() if v.get("pass"))
    return {
        "results": result,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
    }
