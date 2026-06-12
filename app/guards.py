"""Write-guard chain (specs/authorization, design D7).

Every guarded endpoint declares ONE of these dependencies; the chain
order is fixed: identity → effective role → edit lock → (the existing
`require_unsigned` sign-off guard stays in the handlers).

Scope resolution is by route params: version-scoped endpoints carry
`version_id` (path or query), product-scoped ones `product_id`. The
effective role for a product folds in its customer
(`AUTH_STORE.effective_role`), so customer-scope grants Just Work.

Bypass mode (`SMDR2_AUTH_MODE=bypass`, the default) yields an admin
identity — every guard passes, which is exactly the pre-auth behaviour
the test suite encodes.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from app.auth import AUTH_STORE, Identity, get_identity

_RANK = {"viewer": 1, "editor": 2, "admin": 3}


async def current_identity(request: Request) -> Identity:
    return get_identity(request)


def _param(request: Request, name: str) -> str | None:
    return request.path_params.get(name) or request.query_params.get(name)


def _product_for(request: Request) -> str | None:
    """Resolve the target product id from product_id or version_id."""
    pid = _param(request, "product_id")
    if pid:
        return pid
    vid = _param(request, "version_id")
    if vid:
        from app.versions import VERSION_STORE
        v = VERSION_STORE.get(vid)
        if v is None:
            raise HTTPException(status_code=404, detail="version not found")
        return v.product_id
    return None


def effective_role(ident: Identity, product_id: str | None) -> str | None:
    """Role folding in the product's customer scope. `product_id=None`
    checks global grants only (admin endpoints, product creation)."""
    if ident.is_bypass:
        return "admin"
    customer_id = None
    if product_id is not None:
        from app.products import PRODUCT_STORE
        product = PRODUCT_STORE.get(product_id)
        customer_id = product.customer_id if product else None
    return AUTH_STORE.effective_role(
        ident, product_id=product_id, customer_id=customer_id,
    )


def require_role(min_role: str, *, with_lock: bool = False):
    """Dependency factory. `with_lock=True` additionally requires the
    caller to hold the product edit lock (content writes)."""

    async def guard(
        request: Request, ident: Identity = Depends(current_identity),
    ) -> Identity:
        product_id = _product_for(request)
        role = effective_role(ident, product_id)
        if role is None or _RANK[role] < _RANK[min_role]:
            raise HTTPException(
                status_code=403,
                detail=f"requires {min_role} on this "
                       f"{'product' if product_id else 'system'}",
            )
        if with_lock and product_id is not None and not ident.is_bypass:
            holder = AUTH_STORE.lock_holder(product_id)
            if holder != ident.userid:
                raise HTTPException(
                    status_code=423,
                    detail={
                        "error": "edit lock required",
                        "held_by": holder,
                    },
                )
        return ident

    return guard


# The matrix's three rows (specs/authorization):
viewer_guard = require_role("viewer")
editor_guard = require_role("editor", with_lock=True)
editor_guard_nolock = require_role("editor")   # lock acquisition itself
admin_guard = require_role("admin")


async def job_viewer_guard(
    request: Request, ident: Identity = Depends(current_identity),
) -> Identity:
    """Jobs are polled by whoever triggered them: resolve the job's
    version → product and require viewer there. Jobs without a version
    scope (reprocess-all parents) just require authentication. A missing
    job passes through — the handler owns the 404."""
    from app import jobs
    job = jobs.get(request.path_params.get("job_id", ""))
    if job is None or not job.get("version_id"):
        return ident
    from app.versions import VERSION_STORE
    v = VERSION_STORE.get(job["version_id"])
    role = effective_role(ident, v.product_id if v else None)
    if role is None:
        raise HTTPException(status_code=403, detail="requires viewer on this product")
    return ident


def _product_of_template(template_id: str) -> str | None:
    from app.library import LIBRARIES
    from app.versions import VERSION_STORE
    row = LIBRARIES.store.conn.execute(
        "SELECT library_id FROM templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        return None
    vrow = VERSION_STORE.conn.execute(
        "SELECT product_id FROM versions WHERE library_id = ?",
        (row["library_id"],),
    ).fetchone()
    return vrow["product_id"] if vrow else None


async def template_editor_guard(
    request: Request, ident: Identity = Depends(current_identity),
) -> Identity:
    """Template mutations carry only template_id — resolve template →
    library → version → product, then editor + lock. Missing templates
    pass through (handler 404s)."""
    product_id = _product_of_template(request.path_params.get("template_id", ""))
    if product_id is None:
        return ident
    role = effective_role(ident, product_id)
    if role is None or _RANK[role] < _RANK["editor"]:
        raise HTTPException(status_code=403, detail="requires editor on this product")
    if not ident.is_bypass:
        holder = AUTH_STORE.lock_holder(product_id)
        if holder != ident.userid:
            raise HTTPException(
                status_code=423,
                detail={"error": "edit lock required", "held_by": holder},
            )
    return ident


def visible_products(ident: Identity, products: list) -> list:
    """Filter a product list down to the caller's viewer scope —
    grant-less users see an empty system (specs/product-files)."""
    if ident.is_bypass:
        return products
    out = []
    for p in products:
        role = AUTH_STORE.effective_role(
            ident, product_id=p.id, customer_id=p.customer_id,
        )
        if role is not None:
            out.append(p)
    return out
