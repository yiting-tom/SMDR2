"""DRC handoff bundle assembly.

Packages every role-attached DXF + per-file Match JSON in a product
into a zip that conforms to the manifest schema at
``openspec/specs/design-rule-checking/drc-manifest.schema.json``.

The bundle is what the external rule-checking team consumes. Match
JSONs are shipped **per-file** with raw, unprefixed handles — the
``<file_id[:8]>:`` namespacing that ``run_product_rule_check`` applies
for the internal mock checker stays internal and never leaves the
process boundary.

Since the one-library-per-version topology (2026-06-10) the customer
dimension is gone — the manifest instead carries the **version**
(``version_id`` + ``version_label``) whose bindings and Match JSONs
the bundle was built from. Rules remain product-keyed
(``product_id``); the version identifies which snapshot was checked.

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
from app.products import Product
from app.storage import match_path, upload_path
from app.versions import Version


BUNDLE_VERSION = "2.0.0"
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
    version: Version,
    files: list[FileRecord],
    *,
    now: datetime | None = None,
) -> dict:
    """Assemble the manifest dict for one version's role-bound files.

    Pure: no disk I/O. ``files`` MUST already be filtered to
    role-bound records (``dxf_role is not None``) of this version.
    """
    if version.product_id != product.id:
        raise ValueError(
            f"version {version.id!r} belongs to product "
            f"{version.product_id!r}, not {product.id!r}"
        )
    manifest: dict = {
        "bundle_version": BUNDLE_VERSION,
        "product_id": product.id,
        "version_id": version.id,
        "version_label": version.label,
        "exported_at": _format_exported_at(now),
        "files": [_file_entry(f) for f in files],
    }
    if product.name:
        manifest["product_name"] = product.name
    return manifest


def build_bundle(
    product: Product,
    version: Version,
    files: list[FileRecord],
    *,
    now: datetime | None = None,
) -> tuple[bytes, str]:
    """Build the DRC handoff zip for one version of a product.

    Returns ``(zip_bytes, filename)``. ``filename`` is the suggested
    download name (``drc-bundle-<product_id>-<version_id>.zip``).

    Caller responsibilities:

    - ``files`` is the full list of role-bound ``FileRecord``s for
      the version (``dxf_role is not None``). Order is preserved into
      ``manifest.files``.
    - Every binding MUST have ``match_saved == True`` — this is the
      caller's precondition; the function does not re-check it because
      the endpoint handler emits a richer 400 with the offending role
      list before calling here.

    The zip is built in-memory via ``BytesIO``. For the bundle sizes
    SMDR2 produces today (handful of DXFs, each a few MB) this is
    well within memory budget; if real bundles ever push past a few
    hundred MB, swap for a ``SpooledTemporaryFile`` without changing
    the public contract.
    """
    manifest = build_manifest(product, version, files, now=now)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            MANIFEST_FILENAME,
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )
        for rec in files:
            dxf_src = upload_path(rec.id)
            match_src = match_path(version.id, rec.id)
            zf.write(dxf_src, arcname=f"{DXF_DIR}/{rec.id}.dxf")
            zf.write(match_src, arcname=f"{MATCH_DIR}/{rec.id}.json")
    return buf.getvalue(), f"drc-bundle-{product.id}-{version.id}.zip"


def build_bundle_dir(
    product: Product,
    version: Version,
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

    manifest = build_manifest(product, version, files, now=now)
    (dst / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)
    )
    for rec in files:
        shutil.copyfile(upload_path(rec.id), dst / DXF_DIR / f"{rec.id}.dxf")
        shutil.copyfile(
            match_path(version.id, rec.id), dst / MATCH_DIR / f"{rec.id}.json"
        )
    return dst


@contextmanager
def materialise_bundle(
    product: Product,
    version_id: str,
    files: list[FileRecord],
    *,
    now: datetime | None = None,
):
    """Yield a temporary directory containing the materialised handoff
    bundle; clean it up on exit (success OR exception).

    The rule-check worker uses this to hand the external rule function
    a bundle path that exists for the duration of the call and nothing
    longer. ``version_id`` is resolved to a Version fresh from the store
    (worker processes must not rely on parent-process caches).
    """
    from app.versions import VERSION_STORE

    version = VERSION_STORE.get(version_id)
    if version is None:
        raise ValueError(f"version {version_id!r} not found")
    with tempfile.TemporaryDirectory(prefix=f"drc-bundle-{product.id}-") as td:
        bundle_dir = build_bundle_dir(product, version, files, td, now=now)
        yield bundle_dir
