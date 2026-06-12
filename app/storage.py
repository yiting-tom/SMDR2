"""Filesystem layout for SMDR2.

   data/
     uploads/{file_id}.dxf            — original DXF as uploaded by the user
     parsed/{file_id}.json            — cached flatten primitives + bbox + bg
     layer_preview/{file_id}/         — per-layer SVG thumbnails + manifest
       layers.json
       <safe_name>.svg
       primitives.json                — transient: full primitives for Phase 2
       layouts/                       — AutoCAD-tab picker assets (only when
         layouts.json                   a DXF's geometry lives in >1 paper-
         <safe_name>.svg                space layout — see app.jobs)
     library.sqlite                   — templates + file metadata
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# SMDR2_DATA_DIR re-points the whole data tree (the test suite sets it to a
# tmp dir before any app import so runs never touch the real data/). Read
# once at import — every store singleton derives its path from these
# constants.
DATA_DIR = Path(os.environ.get("SMDR2_DATA_DIR", str(PROJECT_ROOT / "data")))
UPLOADS_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
PREMATCH_DIR = DATA_DIR / "prematch"
MATCH_DIR = DATA_DIR / "match"
RULE_CHECK_DIR = DATA_DIR / "rule_check"
LAYER_PREVIEW_DIR = DATA_DIR / "layer_preview"
DB_PATH = DATA_DIR / "library.sqlite"

for d in (UPLOADS_DIR, PARSED_DIR, PREMATCH_DIR, MATCH_DIR, RULE_CHECK_DIR, LAYER_PREVIEW_DIR):
    d.mkdir(parents=True, exist_ok=True)


# Derived artifacts are keyed by (version_id, file_id): two versions can
# share the same DXF bytes (uploads/ is content-addressed, version-free)
# while parsing/matching them differently — re-running version B must
# never overwrite version A's results (old versions stay reproducible).


def upload_path(file_id: str) -> Path:
    return UPLOADS_DIR / f"{file_id}.dxf"


def parsed_path(version_id: str, file_id: str) -> Path:
    return PARSED_DIR / version_id / f"{file_id}.json"


def prematch_path(version_id: str, file_id: str) -> Path:
    return PREMATCH_DIR / version_id / f"{file_id}.json"


def match_path(version_id: str, file_id: str) -> Path:
    return MATCH_DIR / version_id / f"{file_id}.json"


def rule_check_path(version_id: str) -> Path:
    return RULE_CHECK_DIR / f"{version_id}.json"


def layer_preview_dir(version_id: str, file_id: str) -> Path:
    return LAYER_PREVIEW_DIR / version_id / file_id


def layer_manifest_path(version_id: str, file_id: str) -> Path:
    return layer_preview_dir(version_id, file_id) / "layers.json"


def layer_preview_svg_path(version_id: str, file_id: str, safe_name: str) -> Path:
    return layer_preview_dir(version_id, file_id) / f"{safe_name}.svg"


def layer_preview_primitives_path(version_id: str, file_id: str) -> Path:
    """Transient — written by Phase 1, deleted by Phase 2 on success."""
    return layer_preview_dir(version_id, file_id) / "primitives.json"


# ---- Layout (AutoCAD-tab) picker assets ----------------------------------
# Lives in a subdir of the layer-preview dir so a single cleanup of
# layer_preview/{version_id}/{file_id}/ removes both. Subdir keeps layout
# SVGs from colliding with layer SVGs (a layout could share a layer's name).
def layout_preview_dir(version_id: str, file_id: str) -> Path:
    return layer_preview_dir(version_id, file_id) / "layouts"


def layout_manifest_path(version_id: str, file_id: str) -> Path:
    return layout_preview_dir(version_id, file_id) / "layouts.json"


def layout_preview_svg_path(version_id: str, file_id: str, safe_name: str) -> Path:
    return layout_preview_dir(version_id, file_id) / f"{safe_name}.svg"


# ---- Blob keys --------------------------------------------------------------
# String twins of the path helpers, consumed via app.blobstore. Keys ARE the
# DATA_DIR-relative paths, so the local backend writes byte-identical layout
# to today's tree (path-based assertions in tests keep holding) and the S3
# backend needs no mental remapping.
def key_of(path: Path) -> str:
    """DATA_DIR-relative blob key for any artifact path."""
    return path.relative_to(DATA_DIR).as_posix()


def upload_key(file_id: str) -> str:
    return f"uploads/{file_id}.dxf"


def parsed_key(version_id: str, file_id: str) -> str:
    return f"parsed/{version_id}/{file_id}.json"


def prematch_key(version_id: str, file_id: str) -> str:
    return f"prematch/{version_id}/{file_id}.json"


def match_key(version_id: str, file_id: str) -> str:
    return f"match/{version_id}/{file_id}.json"


def rule_check_key(version_id: str) -> str:
    return f"rule_check/{version_id}.json"


def layer_preview_prefix(version_id: str, file_id: str) -> str:
    return f"layer_preview/{version_id}/{file_id}"


def layer_manifest_key(version_id: str, file_id: str) -> str:
    return f"{layer_preview_prefix(version_id, file_id)}/layers.json"


def layer_preview_svg_key(version_id: str, file_id: str, safe_name: str) -> str:
    return f"{layer_preview_prefix(version_id, file_id)}/{safe_name}.svg"


def layer_preview_primitives_key(version_id: str, file_id: str) -> str:
    return f"{layer_preview_prefix(version_id, file_id)}/primitives.json"


def layout_manifest_key(version_id: str, file_id: str) -> str:
    return f"{layer_preview_prefix(version_id, file_id)}/layouts/layouts.json"


def layout_preview_svg_key(version_id: str, file_id: str, safe_name: str) -> str:
    return f"{layer_preview_prefix(version_id, file_id)}/layouts/{safe_name}.svg"

# No version-level prefix helpers on purpose: the bucket has no list API
# (company MinIO rule), so deletes enumerate exact keys from the DB and
# the layer/layout manifests (`_version_artifact_keys` in app.main).
