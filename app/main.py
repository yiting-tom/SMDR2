"""SMDR2 FastAPI entry — versioned product workflow.

Routes (version-aware since 2026-06-10; see openspec change
`add-product-versioning`):
    GET  /                            dashboard (products + versions)
    GET  /viewer/{file_id}?version_id viewer page for one binding
    POST /api/products                create product + first version
    POST /api/products/{pid}/versions new version = clone of source
    POST /api/versions/{vid}/files    upload a DXF into a role
    POST /api/versions/{vid}/sign-off freeze the version
    ...file-centric endpoints keep their paths but require ?version_id=
    (artifacts and binding state key on the (version_id, file_id) pair).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from app import db, jobs
from app.files import (
    AWAITING_LAYOUT,
    DISCOVERING_LAYERS,
    FILE_STORE,
    FileRecord,
    PREPROCESSING,
    READY,
)
from app.library import (
    CLASS_JSON_KEY,
    CLASS_VIEW_CONSTRAINTS,
    LIBRARIES,
    Template,
    build_handle_index,
    collect_entity_kinds,
    collect_entity_points,
)
from app.matching import (
    EntityShape,
    build_entity_shapes,
    diagnose_swap,
    find_matches,
    find_matches_from_pointsets,
)
from app.products import PRODUCT_STORE, VALID_ROLES
from app.versions import DuplicateLabel, VERSION_STORE, Version
from app.drc_bundle import build_bundle
from app.side_regions import normalise_rect, parse_match_key, split_matches_by_side
from app.blobstore import get_blobstore
from app.auth import AUTH_STORE
from app.guards import (
    admin_guard,
    current_identity,
    editor_guard,
    editor_guard_nolock,
    effective_role,
    job_viewer_guard,
    template_editor_guard,
    viewer_guard,
    visible_products,
)
from app.storage import (
    DATA_DIR,
    layer_manifest_key,
    layer_preview_primitives_key,
    layer_preview_svg_key,
    layout_manifest_key,
    layout_preview_svg_key,
    match_key,
    parsed_key,
    prematch_key,
    rule_check_key,
    sign_off_evidence_key,
    upload_key,
)

TEST_DXF = DATA_DIR / "test.dxf"


# Max upload size for a single DXF (env-overridable, mirrors SMDR2_N_JOBS /
# SMDR2_MAX_WORKERS). 300 MB is generous for packaging DXFs and catches the
# "wrong file / corrupted export" mistake without web-scale machinery.
MAX_UPLOAD_BYTES = int(os.environ.get("SMDR2_MAX_UPLOAD_MB", "300")) * 1024 * 1024

# Placeholder identity until the auth change lands: sign-off records this
# as the signer. The auth change swaps it for the OIDC `sub`.
DEV_USER = os.environ.get("SMDR2_DEV_USER", "dev")


def _load_json_or_http(path: str | Path, *, kind: str) -> dict:
    """Read a persisted pipeline-artifact JSON file, turning corruption into
    a clean HTTP 400 (with the artifact kind + path in the detail) instead of
    an uncaught JSONDecodeError → opaque 500. Matches the 400 convention used
    by `upload_rule_check`.

    MUST be called at the route-handler level, NOT inside an `@lru_cache`-wrapped
    helper: `lru_cache` memoizes raised exceptions, so caching the raise would
    re-raise the stale error on later cache hits with the same key.
    """
    try:
        return get_blobstore().get_json(path)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("artifact unreadable: kind=%s path=%s: %s",
                       kind, path, exc)
        raise HTTPException(
            status_code=400,
            detail=f"{kind} file is unreadable/corrupt: {path}: {exc}",
        )


# ---- Cached parsed-JSON + shape index ------------------------------------
# The path embeds (version_id, file_id) since the artifact re-keying, so the
# (path, mtime_ns) cache key is naturally version-scoped.
@lru_cache(maxsize=4)
def _cached_parsed(key: str, token: str) -> dict:  # noqa: ARG001
    return get_blobstore().get_json(key)


@lru_cache(maxsize=4)
def _cached_shapes(key: str, token: str) -> tuple[dict[str, list[int]], dict[str, EntityShape]]:  # noqa: ARG001
    data = _cached_parsed(key, token)
    hi = build_handle_index(data["primitives"])
    shapes = build_entity_shapes(data["primitives"], hi)
    return hi, shapes


# ---- Version / binding resolution + the sign-off write guard --------------
def _resolve_version(version_id: str) -> Version:
    v = VERSION_STORE.get(version_id)
    if v is None:
        raise HTTPException(status_code=404, detail="version not found")
    return v


def require_unsigned(version_id: str) -> Version:
    """Write guard: every mutating operation on a version goes through
    here. Signed-off versions reject with 409 naming who/when. (The auth
    change extends this chain with role / lock checks.)"""
    v = _resolve_version(version_id)
    if v.is_signed_off:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version signed-off",
                "signed_off_by": v.signed_off_by,
                "signed_off_at": v.signed_off_at,
            },
        )
    return v


def _resolve_file(version_id: str, file_id: str, require_ready: bool = True) -> FileRecord:
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    if require_ready and rec.status not in (READY, "checking_rules", "report"):
        raise HTTPException(status_code=425, detail=f"file not ready (status={rec.status})")
    return rec


def _shapes_for(version_id: str, file_id: str) -> tuple[dict[str, list[int]], dict[str, EntityShape]]:
    pk = parsed_key(version_id, file_id)
    try:
        token = get_blobstore().stat(pk)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="parsed file missing on disk")
    return _cached_shapes(pk, token)


def _parsed_for(version_id: str, file_id: str) -> dict:
    pk = parsed_key(version_id, file_id)
    try:
        token = get_blobstore().stat(pk)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="parsed file missing on disk")
    # Guard at the wrapper level (outside `_cached_parsed`'s lru_cache) so a
    # corrupt file yields a contextual 400, not an opaque 500.
    try:
        return _cached_parsed(pk, token)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"parsed file is unreadable/corrupt: {pk}: {exc}",
        )


def _library_for_version(v: Version):
    try:
        return LIBRARIES.get(v.library_id)
    except KeyError:
        raise HTTPException(
            status_code=500,
            detail=f"library {v.library_id!r} missing for version {v.id!r}",
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
    # Find or create the auto-Sample product (with its mandatory first
    # version — C7: no version-less products).
    sample = next((p for p in PRODUCT_STORE.list_all() if p.name == "Sample"), None)
    if sample is None:
        sample, version = VERSION_STORE.create_product("Sample", "v1")
    else:
        version = VERSION_STORE.latest_for_product(sample.id)
        if version is None:
            version = VERSION_STORE.create_first(sample.id, "v1")
    if not get_blobstore().exists(upload_key(fid)):
        with open(TEST_DXF, "rb") as f:
            get_blobstore().put_stream(upload_key(fid), f)
    FILE_STORE.register_content(fid, "test.dxf", TEST_DXF.stat().st_size)
    rec = FILE_STORE.get(version.id, fid)
    if rec is None:
        FILE_STORE.bind(
            version.id, "BD", fid,
            dxf_view="multi", initial_status=DISCOVERING_LAYERS,
        )
        rec = FILE_STORE.get(version.id, fid)
    # If the parsed cache + prematch are already good, leave it alone.
    if (rec is not None
            and rec.status == READY
            and get_blobstore().exists(parsed_key(version.id, fid))
            and get_blobstore().exists(prematch_key(version.id, fid))):
        return
    # Otherwise restart from Phase 1 — discover layers, wait for user.
    FILE_STORE.update_status(version.id, fid, DISCOVERING_LAYERS)
    jobs.submit_discover_layers(version.id, fid)


def _assert_api_routes_guarded(app_: FastAPI) -> None:
    """Default-deny backstop: every /api route must declare a guard
    dependency. The per-route sweep is hand-maintained; this turns a
    forgotten `dependencies=[...]` into a boot failure instead of a
    silently-open endpoint (found the hard way: discover-layers)."""
    from app import guards as g
    guard_fns = {
        g.viewer_guard, g.editor_guard, g.editor_guard_nolock,
        g.admin_guard, g.job_viewer_guard, g.template_editor_guard,
        g.current_identity,
    }
    exempt = {"/api/me"}  # resolves identity in the handler body
    unguarded = []
    for route in app_.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api") or path in exempt:
            continue
        deps = getattr(getattr(route, "dependant", None), "dependencies", [])
        if not any(d.call in guard_fns for d in deps):
            unguarded.append(path)
    if unguarded:
        raise RuntimeError(f"unguarded /api routes: {sorted(set(unguarded))}")


def validate_startup_config() -> None:
    """Fail-fast on missing production secrets/config at boot.

    Without this, a missing OIDC secret only blows up on the first login
    request, and a missing S3 key only on the first upload — both long after
    the pod reports healthy. Validate the *required-together* groups up front
    so a misconfigured deploy never starts serving traffic.

    Env-driven and skipped in bypass mode (dev/tests), so it adds no friction
    to the default local path.
    """
    missing: list[str] = []
    if os.environ.get("SMDR2_AUTH_MODE", "bypass") != "bypass":
        for var in (
            "SESSION_SECRET",
            "OIDC_ISSUER",
            "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET",
            "OIDC_REDIRECT_URI",
        ):
            if not os.environ.get(var):
                missing.append(var)
    # S3 is optional (unset → local store), but a half-configured S3 silently
    # inits with empty credentials and only fails on first use. Require the
    # whole group once the endpoint is set.
    if os.environ.get("S3_ENDPOINT_URL"):
        for var in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
            if not os.environ.get(var):
                missing.append(var)
    if missing:
        logger.error("startup config invalid — missing: %s", ", ".join(missing))
        raise RuntimeError(
            "Missing required configuration (set via environment / secrets): "
            + ", ".join(missing)
        )
    logger.info(
        "startup config OK (auth_mode=%s, s3=%s)",
        os.environ.get("SMDR2_AUTH_MODE", "bypass"),
        "on" if os.environ.get("S3_ENDPOINT_URL") else "local",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_startup_config()
    _assert_api_routes_guarded(_app)
    # Boot-time connectivity summary: one line per external dependency
    # (DB / blob / Keycloak). Non-fatal — logs the state, never blocks boot.
    from app.connectivity import log_startup_connectivity
    log_startup_connectivity()
    # Idempotent BOOTSTRAP_ADMINS seeding (specs/authorization) — the only
    # way the first admin comes into existence.
    admins = [u for u in os.environ.get("BOOTSTRAP_ADMINS", "").split(",") if u.strip()]
    if admins:
        created = AUTH_STORE.bootstrap_admins(admins)
        logger.info("BOOTSTRAP_ADMINS: %d new admin grant(s) from %s",
                    created, admins)
    _ensure_test_dxf_registered()
    # Drain jobs persisted across a restart (the embedded worker otherwise
    # starts lazily on first submit; queued rows from a previous process
    # would sit untouched until then). No-op when SMDR2_EMBEDDED_WORKER=0
    # (k8s webs — the worker pod drains).
    from app.worker_loop import ensure_embedded
    ensure_embedded()
    yield
    jobs.shutdown()
    from app.matching import shutdown_pool
    shutdown_pool()


logger = logging.getLogger(__name__)

# uvicorn configures only its own loggers, so without this the app.* lines
# (startup summary, connectivity, authz denials, …) would propagate to a
# handler-less root and never appear. basicConfig is a no-op when the root
# already has handlers (e.g. under pytest's caplog), so it doesn't double-log.
logging.basicConfig(
    level=os.environ.get("SMDR2_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


app = FastAPI(title="SMDR2", lifespan=lifespan)
# Compress large JSON responses (notably GET /primitives — tens of MB on a
# 400k-circle drawing). Transparent to the browser (response.json() decodes
# gzip automatically); skips responses below the threshold.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ---- Auth (BFF) -----------------------------------------------------------
# specs/auth-session: backend-only OIDC; HttpOnly session cookie; bypass
# mode short-circuits everything (dev / tests).
CSRF_COOKIE = "conform_csrf"


def _cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true")


@app.get("/auth/login")
async def auth_login(next: str = "/"):
    from app import oidc as oidc_mod
    if os.environ.get("SMDR2_AUTH_MODE", "bypass") == "bypass":
        return RedirectResponse("/", status_code=302)
    if not next.startswith("/") or next.startswith("//") or "\\" in next:
        next = "/"  # open-redirect guard: same-origin paths only ("\\" → // in browsers)
    cfg = oidc_mod.OidcConfig.from_env()
    url, cookie = oidc_mod.build_login(cfg, next)
    r = RedirectResponse(url, status_code=302)
    r.set_cookie(
        oidc_mod.STATE_COOKIE, cookie, max_age=600,
        httponly=True, samesite="lax", secure=_cookie_secure(),
    )
    return r


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    from app import oidc as oidc_mod
    from app.auth import SESSION_COOKIE
    state_cookie = request.cookies.get(oidc_mod.STATE_COOKIE)
    if not state_cookie or not code:
        raise HTTPException(status_code=400, detail="login flow state missing")
    try:
        claims, next_path = oidc_mod.exchange_code(
            oidc_mod.OidcConfig.from_env(), code, state, state_cookie,
        )
    except oidc_mod.OidcError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    user, first_login = AUTH_STORE.upsert_user_from_claims(dict(claims))
    token, csrf = AUTH_STORE.create_session(user.userid)
    logger.info("login ok: userid=%s first_login=%s source=oidc",
                user.userid, first_login)
    r = RedirectResponse(next_path, status_code=302)
    r.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=_cookie_secure(),
    )
    # CSRF token rides a readable cookie; the frontend echoes it back in
    # the X-CSRF-Token header on every mutating request.
    r.set_cookie(
        CSRF_COOKIE, csrf, httponly=False, samesite="lax",
        secure=_cookie_secure(),
    )
    r.delete_cookie(oidc_mod.STATE_COOKIE)
    return r


@app.post("/auth/logout")
async def auth_logout(request: Request):
    from app.auth import SESSION_COOKIE
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        AUTH_STORE.delete_session(token)
    body = {"logged_out": True}
    # Killing our local session is only half of a logout: the Keycloak SSO
    # session survives, so the next /auth/login silently re-authes and the
    # user appears never to have logged out. Hand the browser the IdP
    # end-session URL so it terminates the SSO session too. Skipped in
    # bypass mode (OIDC unconfigured → nothing to log out of upstream).
    try:
        from app import oidc as oidc_mod
        body["end_session_url"] = oidc_mod.end_session_url(
            oidc_mod.OidcConfig.from_env(),
        )
    except oidc_mod.OidcError as exc:
        # Local session is gone but we couldn't hand back the IdP end-session
        # URL — the Keycloak SSO session survives ("appears never logged out").
        logger.warning("logout: could not build IdP end-session URL "
                       "(OIDC unconfigured?): %s", exc)
    r = JSONResponse(body)
    r.delete_cookie(SESSION_COOKIE)
    r.delete_cookie(CSRF_COOKIE)
    return r


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness — exempt from auth in every mode (specs/auth-session:
    internal endpoint exemption). Pure: touches no dependency."""
    return {"ok": True}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness — pings the external dependencies (DB / blob / Keycloak)
    and returns 200 when all pass, else 503 with the per-service detail.
    Auth-exempt (non-`/api`). Use this for the k8s readiness probe;
    `/healthz` stays liveness-only."""
    from app.connectivity import check_dependencies
    checks = check_dependencies()
    ok = all(c["ok"] for c in checks.values())
    return JSONResponse({"ok": ok, "checks": checks},
                        status_code=200 if ok else 503)


@app.get("/api/me")
async def me(request: Request):
    from app.auth import get_identity
    ident = get_identity(request)
    grants = [
        g.to_dict() for g in (
            AUTH_STORE.list_grants("user", ident.userid)
            + (AUTH_STORE.list_grants("dept", ident.deptid)
               if ident.deptid else [])
        )
    ]
    return JSONResponse(
        {
            "userid": ident.userid,
            "deptid": ident.deptid,
            "name": ident.name,
            "email": ident.email,
            "description": ident.description,
            "source": ident.source,
            "is_admin": effective_role(ident, None) == "admin",
            "grants": grants,
        },
        # Auth status must never be cached — a logout/login or a grant
        # change has to be seen on the very next request.
        headers={"Cache-Control": "no-store"},
    )


# ---- Pages --------------------------------------------------------------
def _page_identity(request: Request):
    """Page-level auth: API consumers get 401s, but a human hitting a
    page should land on the Keycloak login — redirect with next=.
    Resolves through `dependency_overrides[current_identity]` when set
    (the same seam guarded API routes use), so page tests inject
    identities the same way endpoint tests do."""
    try:
        _resolve_page_identity(request)
        return None
    except HTTPException:
        return RedirectResponse(
            f"/auth/login?next={request.url.path}", status_code=302,
        )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (redir := _page_identity(request)) is not None:
        return redir
    return templates.TemplateResponse(request, "dashboard.html")


def _resolve_page_identity(request: Request):
    """Page-handler identity resolution that still honours
    `dependency_overrides[current_identity]` (test seam) — pages can't
    use Depends() because a 401 must become a login redirect, not JSON."""
    override = app.dependency_overrides.get(current_identity)
    if override is not None:
        return override()
    from app.auth import get_identity
    return get_identity(request)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    from app.guards import effective_role
    try:
        ident = _resolve_page_identity(request)
    except HTTPException:
        return RedirectResponse("/auth/login?next=/admin", status_code=302)
    if effective_role(ident, None) != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return templates.TemplateResponse(request, "admin.html")


@app.get("/viewer/{file_id}", response_class=HTMLResponse)
async def viewer(request: Request, file_id: str, version_id: str):
    if (redir := _page_identity(request)) is not None:
        return redir
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    version = _resolve_version(version_id)
    product = PRODUCT_STORE.get(version.product_id)
    return templates.TemplateResponse(
        request, "viewer.html", {
            "file_id": file_id,
            "file_name": rec.name,
            "version_id": version_id,
            "version_label": version.label,
            "product_name": product.name if product else "",
            "product_id": version.product_id,
            "signed_off": version.is_signed_off,
        }
    )


# ---- Product / version API ------------------------------------------------
class CreateProductRequest(BaseModel):
    name: str
    version_label: str
    customer_id: str = "uncategorized"


class CreateVersionRequest(BaseModel):
    label: str
    clone_from: str | None = None


def _file_payload(rec: FileRecord) -> dict:
    """`rec.to_dict()` plus the dashboard-only `has_layout_options` flag —
    true when a layout (AutoCAD-tab) picker manifest exists on disk for this
    binding, i.e. its geometry spans more than one paper-space layout so the
    operator can (re-)pick which tab to load."""
    d = rec.to_dict()
    d["has_layout_options"] = get_blobstore().exists(layout_manifest_key(rec.version_id, rec.id))
    return d


def _group_files_by_role(files: list[FileRecord]) -> tuple[dict, dict]:
    """Build (`files_by_role`, `files_by_role_all`) from a version's
    binding list.

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
        by_role_all[role] = [_file_payload(r) for r in recs]
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


def _version_payload(v: Version) -> dict:
    """One version's dashboard view: bindings per role, match progress,
    rule-check readiness, and the sign-off badge fields."""
    files = FILE_STORE.list_by_version(v.id)
    by_role, by_role_all = _group_files_by_role(files)
    uploaded = [f for f in files if f.dxf_role is not None]
    return {
        **v.to_dict(),
        "files_by_role": by_role,
        "files_by_role_all": by_role_all,
        "match_progress": {
            "saved": sum(1 for f in uploaded if f.match_saved),
            "total": len(uploaded),
        },
        "ready_for_rule_check": bool(uploaded) and all(f.match_saved for f in uploaded),
        "rule_check_available": get_blobstore().exists(rule_check_key(v.id)),
        "latest_rule_check_job": _project_rule_check_job(
            jobs.latest_rule_check_job(v.id)
        ),
    }


# ---- Admin: customers / grants / audit (specs/authorization) ---------------
class CreateCustomerRequest(BaseModel):
    name: str


class CreateGrantRequest(BaseModel):
    grantee_type: str   # user | dept
    grantee_id: str
    role: str           # admin | editor | viewer
    scope_type: str     # global | customer | product
    scope_id: str = ""


@app.get("/api/customers", dependencies=[Depends(current_identity)])
async def list_customers() -> dict:
    """Readable by anyone authenticated — the dashboard groups products
    by customer; mutation stays admin-only."""
    return {"customers": AUTH_STORE.list_customers()}


@app.post("/api/customers")
async def create_customer(
    req: CreateCustomerRequest, ident=Depends(admin_guard),
) -> dict:
    from app.auth import GrantError
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        cid = AUTH_STORE.create_customer(name, actor=ident.userid)
    except db.IntegrityError:
        raise HTTPException(status_code=409, detail="customer name exists")
    except GrantError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": cid, "name": name}


@app.delete("/api/customers/{customer_id}")
async def delete_customer(
    customer_id: str, ident=Depends(admin_guard),
) -> dict:
    from app.auth import SEED_CUSTOMER_ID, GrantError
    # Seed protection outranks everything — 'uncategorized' is never
    # deletable, with or without products under it.
    if customer_id == SEED_CUSTOMER_ID:
        raise HTTPException(
            status_code=400, detail="the seed customer cannot be deleted",
        )
    # RESTRICT: a customer with products is not deletable (schema doc §2).
    if any(p.customer_id == customer_id for p in PRODUCT_STORE.list_all()):
        raise HTTPException(
            status_code=409, detail="customer still has products",
        )
    try:
        deleted = AUTH_STORE.delete_customer(customer_id, actor=ident.userid)
    except GrantError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="customer not found")
    return {"deleted": customer_id}


@app.get("/api/grants", dependencies=[Depends(admin_guard)])
async def list_grants(
    grantee_type: str | None = None, grantee_id: str | None = None,
) -> dict:
    return {
        "grants": [g.to_dict() for g in AUTH_STORE.list_grants(grantee_type, grantee_id)],
        "known_deptids": AUTH_STORE.known_deptids(),
        "known_users": AUTH_STORE.known_users(),
    }


@app.post("/api/grants")
async def create_grant(
    req: CreateGrantRequest, ident=Depends(admin_guard),
) -> dict:
    from app.auth import GrantError
    # scope_id must point at something real (schema doc §3 — no FK can
    # express an either-or reference).
    if req.scope_type == "customer" and AUTH_STORE.get_customer(req.scope_id) is None:
        raise HTTPException(status_code=400, detail="unknown customer")
    if req.scope_type == "product" and PRODUCT_STORE.get(req.scope_id) is None:
        raise HTTPException(status_code=400, detail="unknown product")
    try:
        g = AUTH_STORE.add_grant(
            grantee_type=req.grantee_type, grantee_id=req.grantee_id.strip(),
            role=req.role, scope_type=req.scope_type, scope_id=req.scope_id,
            granted_by=ident.userid,
        )
    except GrantError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return g.to_dict()


@app.delete("/api/grants/{grant_id}")
async def revoke_grant(grant_id: str, ident=Depends(admin_guard)) -> dict:
    if not AUTH_STORE.revoke_grant(grant_id, actor=ident.userid):
        raise HTTPException(status_code=404, detail="grant not found")
    return {"revoked": grant_id}


@app.get("/api/audit", dependencies=[Depends(admin_guard)])
async def list_audit(
    limit: int = 100,
    product_id: str | None = None,
    actor: str | None = None,
    action: str | None = None,
) -> dict:
    entries = AUTH_STORE.list_audit(min(limit, 500))
    if product_id:
        entries = [e for e in entries if e.get("product_id") == product_id]
    if actor:
        entries = [e for e in entries if e.get("actor") == actor]
    if action:
        entries = [e for e in entries if e.get("action") == action]
    return {"audit": entries}


# ---- Product edit lock (specs/product-edit-lock) ---------------------------
@app.get("/api/products/{product_id}/lock", dependencies=[Depends(viewer_guard)])
async def lock_status(product_id: str) -> dict:
    holder = AUTH_STORE.lock_holder(product_id)
    return {"product_id": product_id, "held_by": holder}


@app.post("/api/products/{product_id}/lock")
async def acquire_lock(
    product_id: str, ident=Depends(editor_guard_nolock),
) -> JSONResponse:
    """Explicit start-editing action. 423 with the holder when occupied;
    re-acquiring one's own lock refreshes the heartbeat."""
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    st = AUTH_STORE.acquire_lock(product_id, ident.userid)
    if not st.acquired:
        return JSONResponse(
            status_code=423,
            content={"held_by": st.held_by, "heartbeat_at": st.heartbeat_at},
        )
    return JSONResponse({"acquired": True, "held_by": ident.userid})


@app.post("/api/products/{product_id}/lock/heartbeat")
async def heartbeat_lock(
    product_id: str, ident=Depends(editor_guard_nolock),
) -> dict:
    ok = AUTH_STORE.heartbeat_lock(product_id, ident.userid)
    if not ok:
        raise HTTPException(status_code=409, detail="lock not held by you")
    return {"ok": True}


@app.delete("/api/products/{product_id}/lock")
async def release_lock(
    product_id: str, force: bool = False, ident=Depends(current_identity),
) -> dict:
    """Owner release, or admin force-release (?force=1, audited)."""
    from app.guards import effective_role
    if force:
        if effective_role(ident, None) != "admin":
            raise HTTPException(status_code=403, detail="force release is admin-only")
        AUTH_STORE.release_lock(product_id, ident.userid, force=True)
        return {"released": True, "forced": True}
    released = AUTH_STORE.release_lock(product_id, ident.userid)
    return {"released": released}


@app.get("/api/products")
async def list_products(ident=Depends(current_identity)) -> dict:
    """Products visible to the caller (viewer scope filtering —
    grant-less users see an empty system), each with its versions."""
    items = []
    for p in visible_products(ident, PRODUCT_STORE.list_all()):
        versions = [
            _version_payload(v) for v in VERSION_STORE.list_by_product(p.id)
        ]
        items.append({
            **p.to_dict(),
            "versions": versions,
            # The caller's role on THIS product (same fn the write guards
            # use) so the client can gate affordances — advisory only, the
            # server still enforces (add-role-based-ui-gating).
            "effective_role": effective_role(ident, p.id),
        })
    return {"products": items}


@app.get("/api/products/{product_id}", dependencies=[Depends(viewer_guard)])
async def get_product(
    product_id: str, ident=Depends(current_identity),
) -> dict:
    p = PRODUCT_STORE.get(product_id)
    if p is None:
        raise HTTPException(status_code=404, detail="product not found")
    return {
        **p.to_dict(),
        "versions": [
            _version_payload(v) for v in VERSION_STORE.list_by_product(p.id)
        ],
        "effective_role": effective_role(ident, p.id),
    }


@app.post("/api/products")
async def create_product(
    req: CreateProductRequest, ident=Depends(admin_guard),
) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    label = req.version_label.strip()
    if not label:
        # Pydantic enforces presence (422 on missing field); this catches
        # whitespace-only labels with the same status family.
        raise HTTPException(status_code=422, detail="version_label required")
    customer_id = (req.customer_id or "uncategorized").strip()
    if AUTH_STORE.get_customer(customer_id) is None:
        raise HTTPException(status_code=400, detail="unknown customer")
    p, v = VERSION_STORE.create_product(name, label, customer_id=customer_id)
    # Seed default classes into the new version's library.
    LIBRARIES.get(v.library_id)
    AUTH_STORE.audit(
        actor=ident.userid, action="product.create",
        target_type="product", target_id=p.id, product_id=p.id,
        detail={"name": name, "customer_id": customer_id},
    )
    return {**p.to_dict(), "versions": [_version_payload(v)]}


def _version_artifact_keys(blobs, version_id: str, file_ids: list[str]) -> list[str]:
    """Every blob key a version owns, enumerated from the DB bindings and
    the layer/layout manifests — never by listing the bucket (the company
    MinIO forbids the list API). Uploads are excluded on purpose: file_ids
    are shared across cloned versions."""
    keys = [rule_check_key(version_id), sign_off_evidence_key(version_id)]
    for fid in file_ids:
        keys += [
            parsed_key(version_id, fid),
            prematch_key(version_id, fid),
            match_key(version_id, fid),
            layer_preview_primitives_key(version_id, fid),
        ]
        for manifest_key, entries_field, svg_key in (
            (layer_manifest_key(version_id, fid), "layers", layer_preview_svg_key),
            (layout_manifest_key(version_id, fid), "layouts", layout_preview_svg_key),
        ):
            try:
                manifest = blobs.get_json(manifest_key)
            except FileNotFoundError:
                continue
            keys.append(manifest_key)
            keys += [
                svg_key(version_id, fid, e["safe_name"])
                for e in manifest.get(entries_field, [])
            ]
    return keys


@app.delete("/api/products/{product_id}")
async def delete_product(product_id: str, ident=Depends(admin_guard)) -> dict:
    """Admin-only. Cascades versions, their libraries, bindings, and
    every version-scoped artifact (keys enumerated, not listed)."""
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    AUTH_STORE.audit(
        actor=ident.userid, action="product.delete",
        target_type="product", target_id=product_id, product_id=product_id,
    )
    blobs = get_blobstore()
    # Bindings vanish with delete_for_product — collect file ids first.
    file_ids = {
        v.id: [f.id for f in FILE_STORE.list_by_version(v.id)]
        for v in VERSION_STORE.list_by_product(product_id)
    }
    removed = VERSION_STORE.delete_for_product(product_id)
    for v in removed:
        LIBRARIES.evict(v.library_id)
        blobs.delete_many(
            _version_artifact_keys(blobs, v.id, file_ids.get(v.id, []))
        )
    PRODUCT_STORE.delete(product_id)
    return {"deleted": product_id, "versions_removed": len(removed)}


@app.get("/api/products/{product_id}/version-diff", dependencies=[Depends(viewer_guard)])
async def get_version_diff(
    product_id: str,
    from_id: str = Query(alias="from"),
    to_id: str = Query(alias="to"),
) -> dict:
    """Compare two versions of the same product (C6). Pure read — works
    on signed-off versions too. Query params: `from`, `to` (version ids)."""
    from app.version_diff import diff_versions
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    v_from = _resolve_version(from_id)
    v_to = _resolve_version(to_id)
    for v in (v_from, v_to):
        if v.product_id != product_id:
            raise HTTPException(
                status_code=400,
                detail=f"version {v.id!r} does not belong to product {product_id!r}",
            )
    return diff_versions(v_from, v_to)


@app.get("/api/products/{product_id}/versions", dependencies=[Depends(viewer_guard)])
async def list_versions(product_id: str) -> dict:
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    return {
        "product_id": product_id,
        "versions": [
            _version_payload(v) for v in VERSION_STORE.list_by_product(product_id)
        ],
    }


@app.post("/api/products/{product_id}/versions", dependencies=[Depends(editor_guard)])
async def create_version(product_id: str, req: CreateVersionRequest) -> dict:
    """New version = clone of `clone_from` (default: the product's most
    recent version): library content + role bindings. Cloned bindings
    reset to `preprocessing` and a preprocess job is submitted for each
    (derived artifacts are per-version and don't exist yet)."""
    if PRODUCT_STORE.get(product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    label = req.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="label required")
    if req.clone_from:
        source = VERSION_STORE.get(req.clone_from)
        if source is None or source.product_id != product_id:
            raise HTTPException(
                status_code=400,
                detail="clone_from must reference a version of this product",
            )
    else:
        source = VERSION_STORE.latest_for_product(product_id)
        if source is None:
            raise HTTPException(status_code=500, detail="product has no versions")
    try:
        ver = VERSION_STORE.create_clone(product_id, label, source)
    except DuplicateLabel:
        raise HTTPException(
            status_code=409,
            detail=f"version label {label!r} already exists for this product",
        )
    LIBRARIES.evict(ver.library_id)
    LIBRARIES.get(ver.library_id)  # hydrate + seed any new default classes
    # Recompute derived artifacts for the carried-over bindings.
    for rec in FILE_STORE.list_by_version(ver.id):
        jobs.submit_preprocess(
            ver.id, rec.id,
            library_id=ver.library_id,
            selected_layers=rec.selected_layers,
        )
    return _version_payload(ver)


_EVIDENCE_MAX_BYTES = 10 * 1024 * 1024  # proof images, not drawings


def _sniff_image_type(data: bytes) -> str | None:
    """MIME from magic bytes — the client's Content-Type is not trusted
    (openspec/changes/add-signoff-evidence D3)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@app.post("/api/versions/{version_id}/sign-off")
async def sign_off_version(
    version_id: str,
    evidence: UploadFile | None = File(None),
    ident=Depends(editor_guard),
) -> dict:
    """Freeze the version. Editors may sign versions in their scope —
    including ones they created (no separation of duties, 2026-06-11).
    Already-signed → 409. `evidence` is an OPTIONAL proof image
    (PNG/JPEG/WebP by magic bytes, ≤10MB); a plain body-less POST keeps
    its exact pre-evidence behaviour."""
    from app.versions import SignedOff
    v0 = _resolve_version(version_id)
    evidence_name = evidence_type = None
    data = b""
    if evidence is not None:
        data = await evidence.read(_EVIDENCE_MAX_BYTES + 1)
        if len(data) > _EVIDENCE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="evidence over 10MB")
        if data:  # an empty part counts as "not attached"
            evidence_type = _sniff_image_type(data)
            if evidence_type is None:
                raise HTTPException(
                    status_code=415,
                    detail="evidence must be a PNG/JPEG/WebP image",
                )
            evidence_name = evidence.filename or "evidence"
    blobs = get_blobstore()
    if evidence_name:
        # Bytes first, metadata with the freeze: losing the (lock-guarded,
        # so near-impossible) sign-off race below rolls the blob back.
        blobs.put_bytes(sign_off_evidence_key(version_id), data)
    try:
        v = VERSION_STORE.sign_off(
            version_id, ident.userid,
            evidence_name=evidence_name, evidence_type=evidence_type,
        )
    except SignedOff as exc:
        if evidence_name:
            blobs.delete(sign_off_evidence_key(version_id))
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version already signed-off",
                "signed_off_by": exc.by,
                "signed_off_at": exc.at,
            },
        )
    AUTH_STORE.audit(
        actor=ident.userid, action="version.sign_off",
        target_type="version", target_id=version_id,
        product_id=v0.product_id, version_id=version_id,
        detail={"evidence": evidence_name},
    )
    return v.to_dict()


class CheckDamRequest(BaseModel):
    check_dam: bool


@app.patch("/api/versions/{version_id}/check-dam")
async def set_version_check_dam(
    version_id: str,
    req: CheckDamRequest,
    ident=Depends(editor_guard),
) -> dict:
    """Toggle the version's 'check DAM' flag. Surfaced in the DRC manifest
    as `check_dam` (read live at bundle build, so the next Rule Check /
    Download All reflects it without re-saving). Rejected on a signed-off
    version — the freeze covers the rule-check input."""
    from app.versions import SignedOff
    v0 = _resolve_version(version_id)
    try:
        v = VERSION_STORE.set_check_dam(version_id, req.check_dam)
    except SignedOff as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version already signed-off",
                "signed_off_by": exc.by,
                "signed_off_at": exc.at,
            },
        )
    AUTH_STORE.audit(
        actor=ident.userid, action="version.set_check_dam",
        target_type="version", target_id=version_id,
        product_id=v0.product_id, version_id=version_id,
        detail={"check_dam": req.check_dam},
    )
    return v.to_dict()


@app.get(
    "/api/versions/{version_id}/sign-off/evidence",
    dependencies=[Depends(viewer_guard)],
)
async def get_sign_off_evidence(version_id: str) -> Response:
    """The sign-off proof image (404 when the version has none)."""
    v = _resolve_version(version_id)
    if not v.evidence_name:
        raise HTTPException(status_code=404, detail="no sign-off evidence")
    try:
        data = get_blobstore().get_bytes(sign_off_evidence_key(version_id))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no sign-off evidence")
    from urllib.parse import quote
    return Response(
        content=data,
        media_type=v.evidence_type or "application/octet-stream",
        # No long cache: the same version URL changes meaning across an
        # admin unsign / re-sign-off (different image, or now 404). The
        # image is tiny and internal, so revalidating every time is free.
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition":
                f"inline; filename*=UTF-8''{quote(v.evidence_name)}",
        },
    )


@app.delete("/api/versions/{version_id}/sign-off")
async def unsign_version(version_id: str, ident=Depends(admin_guard)) -> dict:
    """Reopen a signed version — admin only (specs/authorization). The
    evidence belongs to the revoked sign-off: columns clear in the store,
    the blob goes here (blind delete)."""
    v0 = _resolve_version(version_id)
    v = VERSION_STORE.unsign(version_id)
    get_blobstore().delete(sign_off_evidence_key(version_id))
    AUTH_STORE.audit(
        actor=ident.userid, action="version.unsign",
        target_type="version", target_id=version_id,
        product_id=v0.product_id, version_id=version_id,
    )
    return v.to_dict()


# ---- Version file bindings -------------------------------------------------
@app.post("/api/versions/{version_id}/files", dependencies=[Depends(editor_guard)])
async def upload_version_file(
    version_id: str,
    file: UploadFile = File(...),
    dxf_role: str = Form(...),
    replace_file_id: str | None = Form(None),
    skip_layer_pick: bool = Form(False),
) -> dict:
    """Upload a DXF into a version's role.

    A `(version, role)` can hold any number of DXFs; this endpoint is
    purely additive by default. To replace an existing binding (the
    common "swap this DXF" flow), pass `replace_file_id` — that binding
    is removed first (the content row stays; other versions may still
    reference it). Bytes are content-hash deduplicated across versions.

    `skip_layer_pick` is a dev-mode shortcut: when true the binding
    bypasses Phase 1 entirely and goes straight to Phase 2 with
    `selected_layers=None` (keep every layer).
    """
    version = require_unsigned(version_id)
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
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit "
                f"(SMDR2_MAX_UPLOAD_MB); got {len(content) // (1024 * 1024)} MB"
            ),
        )

    # Optional targeted eviction of a specific binding in this version+role.
    if replace_file_id:
        evictee = FILE_STORE.get(version_id, replace_file_id)
        if evictee is None or evictee.dxf_role != dxf_role:
            raise HTTPException(
                status_code=400,
                detail="replace_file_id does not match a file in this version+role",
            )
        FILE_STORE.unbind(version_id, replace_file_id)
        get_blobstore().delete(match_key(version_id, replace_file_id))

    fid = _file_id_from_bytes(content)
    if not get_blobstore().exists(upload_key(fid)):
        get_blobstore().put_bytes(upload_key(fid), content)
    deduped_bytes = FILE_STORE.content_exists(fid)
    FILE_STORE.register_content(fid, file.filename, len(content))
    initial_status = PREPROCESSING if skip_layer_pick else DISCOVERING_LAYERS
    # bind() replaces any prior binding of this (version, file) — a re-upload
    # of identical bytes to the same version resets its lifecycle so Phase 1
    # (or, on the skip path, Phase 2) re-runs with a fresh layer set.
    FILE_STORE.bind(
        version_id, dxf_role, fid,
        dxf_view="multi", initial_status=initial_status,
    )
    if skip_layer_pick:
        job_id = jobs.submit_preprocess(
            version_id, fid,
            library_id=version.library_id, selected_layers=None,
        )
    else:
        job_id = jobs.submit_discover_layers(version_id, fid)
    return {
        "file_id": fid,
        "version_id": version_id,
        "dxf_role": dxf_role,
        "status": initial_status,
        "deduped": deduped_bytes,
        "job_id": job_id,
    }


@app.delete("/api/versions/{version_id}/files/{file_id}", status_code=204, dependencies=[Depends(editor_guard)])
async def delete_version_file(version_id: str, file_id: str) -> Response:
    """Remove one binding from a version. The content row (and the bytes
    under uploads/) stays — other versions may reference it."""
    require_unsigned(version_id)
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    FILE_STORE.unbind(version_id, file_id)
    # Drop this version's match artifact so it doesn't haunt a future rebind.
    get_blobstore().delete(match_key(version_id, file_id))
    return Response(status_code=204)


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


@app.post("/api/files/{file_id}/unit-override", dependencies=[Depends(editor_guard)])
async def post_unit_override(
    file_id: str, req: UnitOverrideRequest, version_id: str,
) -> JSONResponse:
    """Apply an operator-chosen unit interpretation to a binding. Enqueues
    a background preprocess that re-runs with the new multiplier; the
    response carries the job id and the product/version whose Match
    JSON the recompute will invalidate.

    Returns 202 on success, 400 on bad `unit`, 404 on missing binding,
    409 when a preprocess for this binding is already in flight or the
    version is signed off."""
    from app.dxf import UNIT_TO_SCALE

    if req.unit not in UNIT_TO_SCALE:
        raise HTTPException(
            status_code=400,
            detail=f"unknown unit {req.unit!r}; expected one of {sorted(UNIT_TO_SCALE)}",
        )
    require_unsigned(version_id)
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    if rec.status == AWAITING_LAYOUT:
        raise HTTPException(
            status_code=409,
            detail="file is awaiting an AutoCAD-tab pick; choose a layout first",
        )
    inflight = jobs.find_inflight_preprocess_job(version_id, file_id)
    if inflight is not None:
        return JSONResponse(
            status_code=409,
            content={"detail": "preprocess in flight", "job_id": inflight},
        )
    job_id = jobs.submit_unit_override_preprocess(version_id, file_id, req.unit)
    return JSONResponse(
        status_code=202,
        content={
            "file_id": file_id,
            "version_id": version_id,
            "unit": req.unit,
            "job_id": job_id,
            "affected_products": _affected_product_for_version(version_id),
        },
    )


def _affected_product_for_version(version_id: str) -> list[dict]:
    """The product+version whose Match JSON will be cleared when this
    binding's `applied_scale` changes — shaped as a list for forward
    compatibility."""
    v = VERSION_STORE.get(version_id)
    if v is None:
        return []
    product = PRODUCT_STORE.get(v.product_id)
    if product is None:
        return []
    return [{"id": product.id, "name": product.name, "version_label": v.label}]


@app.patch("/api/files/{file_id}/side-regions", dependencies=[Depends(editor_guard)])
async def patch_side_regions(
    file_id: str, req: SideRegionsRequest, version_id: str,
) -> dict:
    """Persist this binding's top_view / bottom_view / side_view rects.

    Invalidates `data/match/{version_id}/{file_id}.json` (rule-checker
    input) because the saved match keys are no longer in sync with the
    new view labels. Resets `match_saved` so the engineer re-runs Save
    Match after redrawing regions.
    """
    require_unsigned(version_id)
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    top = normalise_rect(req.top_view_rect.model_dump()) if req.top_view_rect else None
    bottom = normalise_rect(req.bottom_view_rect.model_dump()) if req.bottom_view_rect else None
    side = normalise_rect(req.side_view_rect.model_dump()) if req.side_view_rect else None
    # Each binding's region rects are independent — when a (version, role)
    # has multiple DXFs, every binding may legitimately mark its own top /
    # bottom / side rectangles. No cross-file uniqueness check here.
    FILE_STORE.update_side_regions(version_id, file_id, top, bottom, side)

    # Invalidate the saved match JSON — its keys are stale w.r.t. the new
    # rectangles. The user has to re-run Save Match to regenerate.
    get_blobstore().delete(match_key(version_id, file_id))
    FILE_STORE.set_match_saved(version_id, file_id, False)

    return {
        "file_id": file_id,
        "version_id": version_id,
        "top_view_rect": top,
        "bottom_view_rect": bottom,
        "side_view_rect": side,
        "match_saved": False,
    }


@app.get("/api/files", dependencies=[Depends(admin_guard)])
async def list_files() -> dict:
    """Every binding (the operational unit) — kept for debugging."""
    return {"files": [r.to_dict() for r in FILE_STORE.list_all()]}


@app.get("/api/files/{file_id}", dependencies=[Depends(viewer_guard)])
async def get_file(file_id: str, version_id: str) -> dict:
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    return rec.to_dict()


@app.get("/api/jobs/{job_id}", dependencies=[Depends(job_viewer_guard)])
async def get_job(job_id: str) -> dict:
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    return j


# ---- Layer selection (Phase 1 -> Phase 2 gate) --------------------------
def _read_layer_manifest(version_id: str, file_id: str) -> dict | None:
    try:
        return get_blobstore().get_json(layer_manifest_key(version_id, file_id))
    except FileNotFoundError:
        return None


@app.get("/api/files/{file_id}/layers", dependencies=[Depends(viewer_guard)])
async def get_file_layers(file_id: str, version_id: str) -> dict:
    """Manifest + current selection for a binding. 404 if Phase 1 hasn't
    finished yet (no manifest on disk)."""
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    manifest = _read_layer_manifest(version_id, file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layer manifest not available")
    return {
        "file_id": file_id,
        "version_id": version_id,
        "manifest": manifest,
        "selected_layers": rec.selected_layers,
        "status": rec.status,
    }


class LayersConfirmRequest(BaseModel):
    layers: list[str]


@app.post("/api/files/{file_id}/layers", dependencies=[Depends(editor_guard)])
async def confirm_layers(
    file_id: str, req: LayersConfirmRequest, version_id: str,
) -> dict:
    """Persist the user's chosen layer subset and kick off Phase 2."""
    version = require_unsigned(version_id)
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    if not req.layers:
        raise HTTPException(status_code=400, detail="at least one layer required")
    manifest = _read_layer_manifest(version_id, file_id)
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
    FILE_STORE.update_selected_layers(version_id, file_id, ordered)
    FILE_STORE.update_status(version_id, file_id, PREPROCESSING)
    job_id = jobs.submit_preprocess(
        version_id,
        file_id,
        library_id=version.library_id,
        selected_layers=ordered,
    )
    return {
        "file_id": file_id,
        "version_id": version_id,
        "selected_layers": ordered,
        "status": PREPROCESSING,
        "job_id": job_id,
    }


@app.get("/api/files/{file_id}/layer-preview/{safe_name}.svg", dependencies=[Depends(viewer_guard)])
async def get_layer_preview_svg(file_id: str, safe_name: str, version_id: str):
    """Serve one layer's SVG thumbnail. 404 when Phase 1 hasn't completed
    or the requested layer isn't in the binding's manifest."""
    from fastapi.responses import StreamingResponse
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    manifest = _read_layer_manifest(version_id, file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layer manifest not available")
    valid = {layer["safe_name"] for layer in manifest["layers"]}
    if safe_name not in valid:
        raise HTTPException(status_code=404, detail="unknown layer for this file")
    try:
        stream = get_blobstore().open_stream(
            layer_preview_svg_key(version_id, file_id, safe_name)
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="preview SVG missing on disk")
    return StreamingResponse(stream, media_type="image/svg+xml")


@app.post("/api/files/{file_id}/discover-layers", dependencies=[Depends(editor_guard)])
async def trigger_discover_layers(file_id: str, version_id: str) -> dict:
    """Re-run Phase 1 for a binding (e.g. user clicked 'Edit layers' on a
    ready file)."""
    require_unsigned(version_id)
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    FILE_STORE.update_status(version_id, file_id, DISCOVERING_LAYERS)
    job_id = jobs.submit_discover_layers(version_id, file_id)
    return {
        "file_id": file_id, "version_id": version_id,
        "status": DISCOVERING_LAYERS, "job_id": job_id,
    }


# ---- Layout selection (AutoCAD-tab gate, runs before layer selection) ---
# Only files whose geometry lives in >1 paper-space layout (model space
# empty) ever produce a layout manifest and park in `awaiting_layout`;
# model-space and single-tab files skip this gate entirely (the parser
# auto-resolves them). See `app.jobs._discover_layers_worker`.
def _read_layout_manifest(version_id: str, file_id: str) -> dict | None:
    try:
        return get_blobstore().get_json(layout_manifest_key(version_id, file_id))
    except FileNotFoundError:
        return None


@app.get("/api/files/{file_id}/layouts", dependencies=[Depends(viewer_guard)])
async def get_file_layouts(file_id: str, version_id: str) -> dict:
    """Layout (AutoCAD-tab) picker manifest + current choice. 404 when no
    layout picker applies to this binding or discovery hasn't produced
    one yet."""
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    manifest = _read_layout_manifest(version_id, file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layout manifest not available")
    return {
        "file_id": file_id,
        "version_id": version_id,
        "manifest": manifest,
        "chosen_layout": rec.chosen_layout,
        "status": rec.status,
    }


class LayoutConfirmRequest(BaseModel):
    layout: str


@app.post("/api/files/{file_id}/layouts", dependencies=[Depends(editor_guard)])
async def confirm_layout(
    file_id: str, req: LayoutConfirmRequest, version_id: str,
) -> dict:
    """Persist the operator's chosen AutoCAD tab and re-run layer discovery
    against it (Phase 1 → the layer picker). The chosen layout is pinned on
    the binding so every later re-preprocess renders the same tab."""
    require_unsigned(version_id)
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    manifest = _read_layout_manifest(version_id, file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layout manifest not available")
    known = {layout["name"] for layout in manifest["layouts"]}
    if req.layout not in known:
        raise HTTPException(
            status_code=400,
            detail=f"unknown layout for this file: {req.layout!r}",
        )
    FILE_STORE.set_chosen_layout(version_id, file_id, req.layout)
    # Switching tabs swaps the entire entity set, so any saved Match JSON
    # references the OLD tab's entities. Invalidate it (mirrors
    # `patch_side_regions`) so a rule check can't run against a stale match
    # — the operator re-runs Save Match after the new tab is ready.
    if req.layout != rec.chosen_layout:
        get_blobstore().delete(match_key(version_id, file_id))
        FILE_STORE.set_match_saved(version_id, file_id, False)
    FILE_STORE.update_status(version_id, file_id, DISCOVERING_LAYERS)
    job_id = jobs.submit_discover_layers(version_id, file_id, layout_name=req.layout)
    return {
        "file_id": file_id,
        "version_id": version_id,
        "chosen_layout": req.layout,
        "status": DISCOVERING_LAYERS,
        "job_id": job_id,
    }


@app.get("/api/files/{file_id}/layout-preview/{safe_name}.svg", dependencies=[Depends(viewer_guard)])
async def get_layout_preview_svg(file_id: str, safe_name: str, version_id: str):
    """Serve one AutoCAD-tab SVG thumbnail for the layout picker. 404 when
    the binding has no layout manifest or the tab isn't in it."""
    from fastapi.responses import StreamingResponse
    rec = FILE_STORE.get(version_id, file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="file not found in this version")
    manifest = _read_layout_manifest(version_id, file_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="layout manifest not available")
    valid = {layout["safe_name"] for layout in manifest["layouts"]}
    if safe_name not in valid:
        raise HTTPException(status_code=404, detail="unknown layout for this file")
    try:
        stream = get_blobstore().open_stream(
            layout_preview_svg_key(version_id, file_id, safe_name)
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="preview SVG missing on disk")
    return StreamingResponse(stream, media_type="image/svg+xml")


# ---- Classes / templates within a version's library ----------------------
@app.get("/api/versions/{version_id}/classes", dependencies=[Depends(viewer_guard)])
async def classes_by_version(version_id: str) -> dict:
    v = _resolve_version(version_id)
    return {
        "version_id": version_id,
        "library_id": v.library_id,
        "classes": _library_for_version(v).summary(),
    }


# Default `bbox_ratio` applied when the client sets a class to signature
# mode without naming a value. Mirrors `matching.SIGNATURE_DEFAULT_BBOX_RATIO`
# so the chosen number stays consistent between matcher and API surface.
SIGNATURE_DEFAULT_BBOX_RATIO = 0.05


class ClassStrategyRequest(BaseModel):
    strategy: str
    bbox_ratio: float | None = None


@app.put("/api/versions/{version_id}/classes/{class_name}/strategy", dependencies=[Depends(editor_guard)])
async def set_class_strategy(
    version_id: str, class_name: str, req: ClassStrategyRequest,
    ident=Depends(current_identity),
) -> dict:
    v = require_unsigned(version_id)
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
            val = req.bbox_ratio
            if not math.isfinite(val) or val <= 0 or val > 1:
                raise HTTPException(
                    status_code=400,
                    detail="bbox_ratio must be in (0, 1] or null",
                )
            bbox_ratio = val
    lib = _library_for_version(v)
    if not lib.set_strategy(class_name, req.strategy, bbox_ratio):
        raise HTTPException(status_code=404, detail="class not found")
    AUTH_STORE.audit(
        actor=ident.userid, action="class.strategy_change",
        target_type="class", target_id=class_name,
        product_id=v.product_id, version_id=version_id,
        detail={"strategy": req.strategy, "bbox_ratio": bbox_ratio},
    )
    return {
        "version_id": version_id,
        "library_id": v.library_id,
        "class_name": class_name,
        "match_strategy": req.strategy,
        "bbox_ratio": bbox_ratio,
    }


@app.get("/api/classes", dependencies=[Depends(viewer_guard)])
async def classes_for_version(version_id: str) -> dict:
    """Class summary for one version's library (viewer toolbar source)."""
    return await classes_by_version(version_id)


@app.get("/api/versions/{version_id}/templates", dependencies=[Depends(viewer_guard)])
async def list_templates_for_version(version_id: str) -> dict:
    """The version's complete template list (it owns its library 1:1)."""
    v = _resolve_version(version_id)
    lib = _library_for_version(v)
    items = []
    for cls_name, idx, t in lib.all_templates():
        items.append({
            "id": t.id,
            "version_id": version_id,
            "library_id": v.library_id,
            "class_name": cls_name,
            "index": idx,
            "key": f"{cls_name}.{idx}",
            "entity_count": len(t.entity_point_sets),
            "vertex_count": sum(len(e) for e in t.entity_point_sets),
            "bbox": list(t.bbox),
            "centroid": list(t.centroid),
            "entity_point_sets": t.entity_point_sets,
        })
    return {"templates": items, "version_id": version_id, "library_id": v.library_id}


@app.get("/api/templates", dependencies=[Depends(viewer_guard)])
async def list_templates(version_id: str) -> dict:
    """Alias for the version-scoped templates endpoint."""
    return await list_templates_for_version(version_id)


def _find_template_owner(template_id: str):
    """Locate (library, version) owning a template id. Returns (lib, version)
    or (None, None)."""
    for lib_summary in LIBRARIES.list_summaries():
        lib = LIBRARIES.get(lib_summary["id"])
        if lib.find_template(template_id) is not None:
            return lib, VERSION_STORE.get_by_library(lib.library_id)
    return None, None


@app.delete("/api/templates/{template_id}", dependencies=[Depends(template_editor_guard)])
async def delete_template(
    template_id: str, ident=Depends(current_identity),
) -> dict:
    # Search across all libraries — template ids are globally unique (UUIDs).
    lib, version = _find_template_owner(template_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="template not found")
    if version is not None:
        require_unsigned(version.id)
    lib.delete_template(template_id)
    AUTH_STORE.audit(
        actor=ident.userid, action="template.delete",
        target_type="template", target_id=template_id,
        product_id=version.product_id if version else None,
        version_id=version.id if version else None,
    )
    return {"deleted": template_id, "library_id": lib.library_id}


class MoveTemplateRequest(BaseModel):
    class_name: str


@app.patch("/api/templates/{template_id}", dependencies=[Depends(template_editor_guard)])
async def patch_template(
    template_id: str, req: MoveTemplateRequest, ident=Depends(current_identity),
) -> dict:
    if not req.class_name:
        raise HTTPException(status_code=400, detail="class_name required")
    lib, version = _find_template_owner(template_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="template not found")
    if version is not None:
        require_unsigned(version.id)
    if req.class_name not in {c["name"] for c in lib.summary()}:
        lib.add_class(req.class_name)
    lib.move_template(template_id, req.class_name)
    AUTH_STORE.audit(
        actor=ident.userid, action="template.modify",
        target_type="template", target_id=template_id,
        product_id=version.product_id if version else None,
        version_id=version.id if version else None,
        detail={"moved_to": req.class_name},
    )
    return {"id": template_id, "class_name": req.class_name,
            "library_id": lib.library_id}


# ---- Per-file: primitives / match / commit / scan-all -------------------
@app.get("/api/files/{file_id}/primitives", dependencies=[Depends(viewer_guard)])
async def primitives(file_id: str, version_id: str) -> dict:
    _resolve_file(version_id, file_id)
    data = _parsed_for(version_id, file_id)
    return {
        "primitives": data["primitives"],
        "bbox": data["bbox"],
        "background": data["background"],
        "count": len(data["primitives"]),
        # Which AutoCAD tab these primitives came from ("Model" or a
        # paper-space layout name); None on legacy parsed files. Lets the
        # viewer surface a "loaded from <tab>" hint.
        "source_layout": data.get("source_layout"),
    }


# Sync (not async) so FastAPI dispatches to the worker thread pool — the
# ~1–2 s `build_entity_shapes` on a 25k-entity drawing must NOT block the
# async event loop. Viewer fires this fire-and-forget after `/primitives`
# returns so the user's first /match scan finds the LRU cache populated.
@app.post("/api/files/{file_id}/warm-shapes", dependencies=[Depends(viewer_guard)])
def warm_shapes(file_id: str, version_id: str) -> dict:
    _resolve_file(version_id, file_id)
    _, shapes = _shapes_for(version_id, file_id)
    return {"entity_count": len(shapes)}


@app.get("/api/files/{file_id}/debug-radius-buckets", dependencies=[Depends(viewer_guard)])
async def debug_radius_buckets(file_id: str, version_id: str) -> dict:
    """Diagnostic dump for the single-CIRCLE radius-bucket fast path.

    Compares each version-library template's recomputed-via-from_points
    radius with the drawing's analytical-radius bucket distribution.
    Used to chase scan-all-misses-everything bugs that don't reproduce
    on small fixtures."""
    from app.matching import (
        _get_radius_buckets,
        _radius_bucket_key,
        EntityShape,
    )
    _resolve_file(version_id, file_id)
    v = _resolve_version(version_id)
    _, shapes = _shapes_for(version_id, file_id)
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

    classes, _, templates_by_class = LIBRARIES.store.load_library(v.library_id)

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
        "version_id": version_id,
        "library_id": v.library_id,
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


@app.post("/api/files/{file_id}/match", dependencies=[Depends(editor_guard)])
async def match(file_id: str, req: MatchRequest, version_id: str) -> dict:
    if not req.handles:
        raise HTTPException(status_code=400, detail="empty template")
    _resolve_file(version_id, file_id)
    v = _resolve_version(version_id)
    _, shapes = _shapes_for(version_id, file_id)
    missing = [h for h in req.handles if h not in shapes]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown handles: {missing[:5]}")
    strategy = "chamfer"
    bbox_ratio: float | None = None
    if req.class_name:
        strategy, bbox_ratio = _library_for_version(v).strategy_of(req.class_name)
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


@app.post("/api/files/{file_id}/match-swap", dependencies=[Depends(editor_guard)])
async def match_swap(file_id: str, req: MatchSwapRequest, version_id: str) -> dict:
    """Diagnostic: run find_matches with pattern_a as template AND with
    pattern_b as template, and dump per-pair gate + alignment breakdown so an
    asymmetric "A finds B but B doesn't find A" outcome can be pinpointed.

    Curl from the viewer when the bug shows up — body is two handle lists.
    """
    if not req.pattern_a or not req.pattern_b:
        raise HTTPException(
            status_code=400, detail="both pattern_a and pattern_b required",
        )
    _resolve_file(version_id, file_id)
    v = _resolve_version(version_id)
    _, shapes = _shapes_for(version_id, file_id)
    missing = [h for h in req.pattern_a + req.pattern_b if h not in shapes]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"unknown handles: {missing[:5]}",
        )
    strategy = "chamfer"
    bbox_ratio: float | None = None
    if req.class_name:
        strategy, bbox_ratio = _library_for_version(v).strategy_of(req.class_name)
    return diagnose_swap(
        req.pattern_a, req.pattern_b, shapes,
        strategy=strategy, bbox_ratio=bbox_ratio,
    )


class CommitRequest(BaseModel):
    class_name: str
    handles: list[str]


@app.post("/api/files/{file_id}/commit", dependencies=[Depends(editor_guard)])
async def commit(
    file_id: str, req: CommitRequest, version_id: str,
    ident=Depends(current_identity),
) -> dict:
    if not req.handles:
        raise HTTPException(status_code=400, detail="empty template")
    v = require_unsigned(version_id)
    _resolve_file(version_id, file_id)
    lib = _library_for_version(v)
    if req.class_name not in {c["name"] for c in lib.summary()}:
        lib.add_class(req.class_name)
    data = _parsed_for(version_id, file_id)
    handle_index, _ = _shapes_for(version_id, file_id)
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
    stored, already_existed = lib.add_template_for_file(tmpl)
    if not already_existed:
        AUTH_STORE.audit(
            actor=ident.userid, action="template.add",
            target_type="template", target_id=stored.id,
            product_id=v.product_id, version_id=version_id,
            detail={"class_name": stored.class_name},
        )
    return {
        "template_id": stored.id,
        "class_name": stored.class_name,
        "version_id": version_id,
        "library_id": v.library_id,
        "count": lib.count(stored.class_name),
        "already_existed": already_existed,
    }


@app.get("/api/files/{file_id}/scan-all", dependencies=[Depends(viewer_guard)])
def scan_all(file_id: str, version_id: str) -> dict:  # sync → threadpool
    """Overlay-only preview of "what class does each handle belong to".

    Runs the *same* pipeline as `save_match_json` — view split via
    `split_matches_by_side` — so the overlay's per-class colouring matches
    what Save Match would persist to disk.

    Frozen versions reject (409): the sign-off contract is "results no
    longer change", and scan-all participates in the editing loop.

    Declared **sync** on purpose: the body is CPU-bound with no `await`, so as
    an `async def` it would run on the event loop and block every other request
    for the whole scan. A plain `def` path operation is dispatched to
    Starlette's threadpool, keeping the event loop free.
    """
    v = require_unsigned(version_id)
    rec = _resolve_file(version_id, file_id)
    # Fresh store read (not the cached Library) so templates committed by
    # other files of this version since hydration are visible.
    classes, configs_by_class, templates_by_class = LIBRARIES.store.load_library(
        v.library_id
    )
    _, shapes = _shapes_for(version_id, file_id)

    # View-rect dispatch (mirror of save_match_json). Used by
    # split_matches_by_side and by the skip-when-impossible gate below.
    rect_for = {
        "top_view": rec.top_view_rect,
        "bottom_view": rec.bottom_view_rect,
        "side_view": rec.side_view_rect,
    }

    # Prefixed-key dict, same shape save_match_json builds before
    # persistence.
    out: dict[str, list[list[str]]] = {}

    for cls_name in classes:
        # Skip-when-impossible: class is view-constrained and none of
        # its allowed view rects is set on the binding → every match would
        # be dropped by split_matches_by_side anyway. Pure perf gate.
        allowed = CLASS_VIEW_CONSTRAINTS.get(cls_name)
        if allowed is not None and not any(rect_for[v_] is not None for v_ in allowed):
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
            for k, vv in grouped.items():
                out.setdefault(k, []).extend(vv)

    # Collapse prefixed-keys back to the flat {display_name: handles}
    # shape the overlay expects.
    display_by_snake = {v_: k for k, v_ in CLASS_JSON_KEY.items()}
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

    by_class = {k: sorted(vv) for k, vv in by_class_sets.items()}
    return {
        "by_class": by_class,
        "total": sum(len(vv) for vv in by_class.values()),
    }


# ---- Pre-match cache (written by preprocess worker) ---------------------
@app.get("/api/files/{file_id}/prematch", dependencies=[Depends(viewer_guard)])
async def prematch(file_id: str, version_id: str) -> dict:
    _resolve_file(version_id, file_id)
    pk = prematch_key(version_id, file_id)
    if not get_blobstore().exists(pk):
        # The worker didn't get to this step yet — return empty.
        return {"by_class": {}, "total": 0, "stale": True}
    return _load_json_or_http(pk, kind="prematch")


# ---- Match JSON ----------------------------------------------------------
# Format the downstream rule-checker expects:
#   { "<view>.<class_snake>.<template_index>": [[handle, ...], ...], ... }
# The endpoint is async: it submits a `save_match` job to the worker
# pool and returns 202 + {job_id, file_id} immediately. The result lives
# on `GET /api/jobs/{job_id}` once the job reaches status=done;
# `match_saved` flips at that point too.
@app.post("/api/files/{file_id}/match-json", dependencies=[Depends(editor_guard)])
async def save_match_json(file_id: str, version_id: str) -> JSONResponse:
    require_unsigned(version_id)
    _resolve_file(version_id, file_id)
    if not get_blobstore().exists(parsed_key(version_id, file_id)):
        raise HTTPException(
            status_code=500, detail="parsed file missing on disk",
        )
    job_id = jobs.submit_save_match(version_id, file_id)
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "file_id": file_id, "version_id": version_id},
    )


@app.get("/api/files/{file_id}/match-json", dependencies=[Depends(viewer_guard)])
async def get_match_json(file_id: str, version_id: str) -> dict:
    _resolve_file(version_id, file_id)
    mk = match_key(version_id, file_id)
    if not get_blobstore().exists(mk):
        raise HTTPException(status_code=404, detail="match JSON not yet generated")
    return _load_json_or_http(mk, kind="match")


# ---- Rule checking (version-scoped bundle, product-keyed rules) ----------
@app.post("/api/versions/{version_id}/rule-check", dependencies=[Depends(editor_guard)])
async def run_rule_check(version_id: str) -> JSONResponse:
    """Submit a version's DRC job. Returns 202 + `{job_id}`; the
    front-end polls `GET /api/jobs/{job_id}` for completion. Every
    binding's `match_saved` must be true; otherwise we 400 with a list
    of missing roles. Signed-off versions reject (409) — frozen results
    must not change."""
    version = require_unsigned(version_id)
    files = [f for f in FILE_STORE.list_by_version(version_id) if f.dxf_role]
    if not files:
        raise HTTPException(status_code=400, detail="no DXFs uploaded to this version yet")
    missing = [f.dxf_role for f in files if not f.match_saved]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"these roles still need Save Match: {', '.join(sorted(missing))}",
        )

    # Validate that every role-bound file's Match JSON and DXF exist on
    # disk before submitting — the worker materialises a handoff bundle
    # from these and would fail late otherwise.
    blobs = get_blobstore()
    for f in files:
        if not blobs.exists(match_key(version_id, f.id)):
            raise HTTPException(
                status_code=400,
                detail=f"{f.dxf_role}: Match JSON missing at {f.id}.json",
            )
        if not blobs.exists(upload_key(f.id)):
            raise HTTPException(
                status_code=500,
                detail=f"{f.dxf_role}: source DXF missing at {f.id}.dxf",
            )

    job_id = jobs.submit_rule_check(
        version.product_id, version_id, [f.id for f in files]
    )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "product_id": version.product_id,
            "version_id": version_id,
        },
    )


@app.get("/api/versions/{version_id}/rule-check", dependencies=[Depends(viewer_guard)])
async def get_rule_check(version_id: str) -> dict:
    """The version's most recently persisted rule-check result — readable
    indefinitely, signed-off or not (C4: old versions stay reviewable)."""
    version = _resolve_version(version_id)
    rk = rule_check_key(version_id)
    if not get_blobstore().exists(rk):
        raise HTTPException(status_code=404, detail="rule check not yet run")
    result = _load_json_or_http(rk, kind="rule-check")
    # Re-validate the persisted payload against the envelope contract so a
    # structurally-corrupt-but-parseable file surfaces loudly (400) instead
    # of producing silent wrong pass/fail counts.
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
        "product_id": version.product_id,
        "version_id": version_id,
        "results": result,
        "rule_count": len(result),
        "pass_count": n_pass,
        "fail_count": len(result) - n_pass,
    }


@app.post("/api/versions/{version_id}/rule-check/upload", dependencies=[Depends(editor_guard)])
async def upload_rule_check(version_id: str, request: Request) -> dict:
    """Dev-only: accept a hand-crafted RuleChecking JSON, validate it
    against the envelope contract, and persist it as if the worker had
    written it. Lets the external team iterate on output shape without
    running their pipeline."""
    from app.rule_check import RuleCheckOutputError, _validate_envelope

    require_unsigned(version_id)
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")
    try:
        _validate_envelope(payload)
    except RuleCheckOutputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rk = rule_check_key(version_id)
    get_blobstore().put_json(rk, payload, ensure_ascii=False, indent=2)
    n_pass = sum(1 for v in payload.values() if v.get("pass"))
    return {
        "version_id": version_id,
        "saved_to": rk,
        "rule_count": len(payload),
        "pass_count": n_pass,
        "fail_count": len(payload) - n_pass,
    }


# ---- DRC handoff bundle (external rule-check team consumes this) --------
@app.get("/api/versions/{version_id}/drc-bundle", dependencies=[Depends(viewer_guard)])
async def get_drc_bundle(version_id: str) -> Response:
    """Return a zip bundle of every role-bound DXF + per-file Match
    JSON + a manifest the external rule-checking team consumes. Each
    DXF stays in its own coordinate space; every Match JSON ships with
    raw DXF handles.

    Preconditions match `POST .../rule-check` minus the freeze guard —
    exporting a signed-off version's bundle is a read."""
    version = _resolve_version(version_id)
    product = PRODUCT_STORE.get(version.product_id)
    if product is None:
        raise HTTPException(status_code=500, detail="owning product missing")
    files = [f for f in FILE_STORE.list_by_version(version_id) if f.dxf_role]
    if not files:
        raise HTTPException(
            status_code=400,
            detail="no DXFs uploaded to this version yet",
        )
    missing = [f.dxf_role for f in files if not f.match_saved]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"these roles still need Save Match: {', '.join(sorted(missing))}",
        )
    zip_bytes, filename = build_bundle(product, version, files)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---- Developer-mode parameter overrides ---------------------------------
# Process-wide, in-memory only. See `app/dev_overrides.py` and the
# `expose-dev-parameter-overrides` OpenSpec change for the contract.
# Process-local state is exactly what multi-replica deployments must not
# expose (two pods would silently diverge) — production disables the whole
# surface with SMDR2_DEV_TOOLS=0.
def _require_dev_tools() -> None:
    if os.environ.get("SMDR2_DEV_TOOLS", "1") == "0":
        raise HTTPException(status_code=404, detail="dev tools disabled")


@app.get("/api/dev/settings", dependencies=[Depends(admin_guard)])
async def dev_settings_get() -> dict:
    _require_dev_tools()
    from app import dev_overrides
    return {"settings": dev_overrides.read_state()}


@app.post("/api/dev/settings", dependencies=[Depends(admin_guard)])
async def dev_settings_post(payload: dict) -> dict:
    _require_dev_tools()
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


@app.post("/api/dev/reprocess-all", dependencies=[Depends(admin_guard)])
async def dev_reprocess_all() -> dict:
    _require_dev_tools()
    job_id = jobs.submit_reprocess_all()
    return {"job_id": job_id}
