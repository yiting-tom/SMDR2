"""DRC handoff bundle assembly.

Packages every role-attached DXF + per-file Match JSON in a product
into a zip that conforms to the manifest schema at
``openspec/specs/design-rule-checking/drc-manifest.schema.json``.

The bundle is what the external rule-checking team consumes. Match
JSONs are shipped **per-file** with raw, unprefixed handles — the
``<file_id[:8]>:`` namespacing that ``run_product_rule_check`` applies
for the internal mock checker stays internal and never leaves the
process boundary.

Layout inside the zip:

    manifest.json
    dxfs/<file_id>.dxf
    match/<file_id>.json

Paths are stable (`<file_id>.dxf`, not the original upload filename)
so the manifest references stay deterministic across exports.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from app.files import FileRecord
from app.products import Product
from app.storage import match_path, upload_path


BUNDLE_VERSION = "1.1.0"
MANIFEST_FILENAME = "manifest.json"
DXF_DIR = "dxfs"
MATCH_DIR = "match"


def _format_exported_at(now: datetime | None) -> str:
    """UTC ISO-8601 with second precision (e.g. ``2026-05-19T07:30:00Z``).

    Accepts naive or aware datetimes; naive inputs are assumed UTC.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file_entry(rec: FileRecord) -> dict:
    return {
        "role": rec.dxf_role,
        "file_id": rec.id,
        "dxf": f"{DXF_DIR}/{rec.id}.dxf",
        "match_json": f"{MATCH_DIR}/{rec.id}.json",
    }


def build_manifest(
    product: Product,
    files: list[FileRecord],
    *,
    now: datetime | None = None,
) -> dict:
    """Assemble the manifest dict for a product's role-attached files.

    Pure: no disk I/O. ``files`` MUST already be filtered to
    role-attached records (``dxf_role is not None``).
    """
    # RING and LID are mutually exclusive per product; the upload
    # handler enforces this at write time. Fail loudly here if upstream
    # data ever drifts so we never ship a bundle that violates the
    # external rule-checking team's contract.
    roles = {f.dxf_role for f in files}
    if "RING" in roles and "LID" in roles:
        raise ValueError(
            f"product {product.id!r} has both RING and LID files; "
            "these are mutually exclusive — refusing to build manifest"
        )
    manifest: dict = {
        "bundle_version": BUNDLE_VERSION,
        "product_id": product.id,
        "exported_at": _format_exported_at(now),
        "files": [_file_entry(f) for f in files],
    }
    if product.name:
        manifest["product_name"] = product.name
    return manifest


def build_bundle(
    product: Product,
    files: list[FileRecord],
    *,
    now: datetime | None = None,
) -> tuple[bytes, str]:
    """Build the DRC handoff zip for one product.

    Returns ``(zip_bytes, filename)``. ``filename`` is the suggested
    download name (``drc-bundle-<product_id>.zip``).

    Caller responsibilities:

    - ``files`` is the full list of role-attached ``FileRecord``s for
      the product (``dxf_role is not None``). Order is preserved into
      ``manifest.files``.
    - Every file MUST have ``match_saved == True`` — this is the
      caller's precondition; the function does not re-check it because
      the endpoint handler emits a richer 400 with the offending role
      list before calling here.

    The zip is built in-memory via ``BytesIO``. For the bundle sizes
    SMDR2 produces today (handful of DXFs, each a few MB) this is
    well within memory budget; if real bundles ever push past a few
    hundred MB, swap for a ``SpooledTemporaryFile`` without changing
    the public contract.
    """
    manifest = build_manifest(product, files, now=now)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            MANIFEST_FILENAME,
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )
        for rec in files:
            dxf_src = upload_path(rec.id)
            match_src = match_path(rec.id)
            zf.write(dxf_src, arcname=f"{DXF_DIR}/{rec.id}.dxf")
            zf.write(match_src, arcname=f"{MATCH_DIR}/{rec.id}.json")
    return buf.getvalue(), f"drc-bundle-{product.id}.zip"
