"""DRC handoff bundle assembly.

Packages every role-attached DXF + per-file Match JSON in a product
into a zip that conforms to the manifest schema at
``openspec/specs/design-rule-checking/drc-manifest.schema.json``.

The bundle is what the external rule-checking team consumes. Match
JSONs are shipped **per-file** with raw, unprefixed handles — the
``<file_id[:8]>:`` namespacing that ``run_product_rule_check`` applies
for the internal mock checker stays internal and never leaves the
process boundary.

The manifest also surfaces the **customer** dimension at the top
level via ``customer_id`` (the SMDR2 ``library_id`` the product is
bound to) and the optional ``customer`` name. ``build_manifest``
resolves the name from the ``LIBRARIES`` registry at export time;
if the library is missing the builder raises ``ValueError`` rather
than emitting a manifest with a silently-dropped customer.

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
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.dxf import SCALE_TO_UNIT
from app.files import FileRecord
from app.library import LIBRARIES
from app.products import Product
from app.storage import match_path, upload_path


BUNDLE_VERSION = "1.4.0"
MANIFEST_FILENAME = "manifest.json"
DXF_DIR = "dxfs"
MATCH_DIR = "match"

# Manifest unit vocabulary (ASCII `um`, not the internal Unicode `μm`). `km`
# only ever appears as `original_unit` — the operator picker offers no km.
#
# Internal unit spelling (`app.dxf.UNIT_TO_SCALE` keys / `user_unit_override`)
# → manifest spelling. Only μm diverges; the rest are identity.
_INTERNAL_TO_MANIFEST_UNIT = {
    "mm": "mm", "cm": "cm", "m": "m", "inch": "inch", "μm": "um",
}

# DXF `$INSUNITS` code → manifest unit string. Codes outside this map
# (0 unitless, 2 foot, 3 miles, … or None) report as null.
_INSUNITS_TO_MANIFEST_UNIT = {
    1: "inch", 4: "mm", 5: "cm", 6: "m", 7: "km", 13: "um",
}


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


def _original_unit(rec: FileRecord) -> str | None:
    """The DXF's declared `$INSUNITS` mapped to the manifest unit vocabulary,
    or None when the header is unitless / unsupported / missing."""
    return _INSUNITS_TO_MANIFEST_UNIT.get(rec.insunits)


def _user_unit(rec: FileRecord) -> str | None:
    """The unit currently in force for the operator: the explicit unit-picker
    override if set, otherwise the effective unit implied by the applied
    auto-rescale factor. None when no named unit applies (a unitless file the
    detector rescaled to a non-standard factor, e.g. ×0.01 / ×100)."""
    internal = rec.user_unit_override or SCALE_TO_UNIT.get(rec.applied_scale)
    return _INTERNAL_TO_MANIFEST_UNIT.get(internal) if internal else None


def _views(rec: FileRecord) -> list[str]:
    """The views the DXF carries — one per side-region rectangle the operator
    has set, in canonical order top → bottom → side. Empty when none are set.
    Values drop the `_view` suffix the Match JSON key prefixes carry."""
    pairs = (
        ("top", rec.top_view_rect),
        ("bottom", rec.bottom_view_rect),
        ("side", rec.side_view_rect),
    )
    return [name for name, rect in pairs if rect]


def _file_entry(rec: FileRecord) -> dict:
    return {
        "role": rec.dxf_role,
        "file_id": rec.id,
        "dxf": f"{DXF_DIR}/{rec.id}.dxf",
        "match_json": f"{MATCH_DIR}/{rec.id}.json",
        "user_unit": _user_unit(rec),
        "original_unit": _original_unit(rec),
        "view": _views(rec),
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
    # Customer = the library the product is bound to. Resolve via the
    # registry's store rather than the cached Library object so we
    # don't pay for the templates load on the export path.
    library_row = LIBRARIES.store.get_library(product.library_id)
    if library_row is None:
        raise ValueError(
            f"library {product.library_id!r} not found for product "
            f"{product.id!r}; refusing to build manifest with missing customer"
        )
    manifest: dict = {
        "bundle_version": BUNDLE_VERSION,
        "product_id": product.id,
        "customer_id": product.library_id,
        "exported_at": _format_exported_at(now),
        "files": [_file_entry(f) for f in files],
    }
    if product.name:
        manifest["product_name"] = product.name
    customer_name = library_row["name"] or ""
    if customer_name:
        manifest["customer"] = customer_name
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


def build_bundle_dir(
    product: Product,
    files: list[FileRecord],
    dst_dir: str | Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Materialise the DRC handoff bundle as a directory tree.

    Writes the same layout :func:`build_bundle` packages into its zip —
    ``manifest.json`` at the root plus ``dxfs/<file_id>.dxf`` and
    ``match/<file_id>.json`` per file — directly under ``dst_dir``.
    Used by the rule-check worker to hand the external rule function an
    on-disk bundle without paying for the zip step.

    The DXF and Match JSON files are byte-copied from their on-disk
    sources so the external function sees the same content hash SMDR2
    ingested.
    """
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / DXF_DIR).mkdir(parents=True, exist_ok=True)
    (dst / MATCH_DIR).mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(product, files, now=now)
    (dst / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )
    for rec in files:
        shutil.copyfile(upload_path(rec.id), dst / DXF_DIR / f"{rec.id}.dxf")
        shutil.copyfile(match_path(rec.id), dst / MATCH_DIR / f"{rec.id}.json")
    return dst


@contextmanager
def materialise_bundle(
    product: Product,
    files: list[FileRecord],
    *,
    now: datetime | None = None,
):
    """Yield a temporary directory containing the materialised handoff
    bundle; clean it up on exit (success OR exception).

    The rule-check worker uses this to hand the external rule function
    a bundle path that exists for the duration of the call and nothing
    longer.
    """
    with tempfile.TemporaryDirectory(prefix=f"drc-bundle-{product.id}-") as td:
        bundle_dir = build_bundle_dir(product, files, td, now=now)
        yield bundle_dir
