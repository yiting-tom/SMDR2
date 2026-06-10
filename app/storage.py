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


def upload_path(file_id: str) -> Path:
    return UPLOADS_DIR / f"{file_id}.dxf"


def parsed_path(file_id: str) -> Path:
    return PARSED_DIR / f"{file_id}.json"


def prematch_path(file_id: str) -> Path:
    return PREMATCH_DIR / f"{file_id}.json"


def match_path(file_id: str) -> Path:
    return MATCH_DIR / f"{file_id}.json"


def rule_check_path(file_id: str) -> Path:
    return RULE_CHECK_DIR / f"{file_id}.json"


def layer_preview_dir(file_id: str) -> Path:
    return LAYER_PREVIEW_DIR / file_id


def layer_manifest_path(file_id: str) -> Path:
    return layer_preview_dir(file_id) / "layers.json"


def layer_preview_svg_path(file_id: str, safe_name: str) -> Path:
    return layer_preview_dir(file_id) / f"{safe_name}.svg"


def layer_preview_primitives_path(file_id: str) -> Path:
    """Transient — written by Phase 1, deleted by Phase 2 on success."""
    return layer_preview_dir(file_id) / "primitives.json"


# ---- Layout (AutoCAD-tab) picker assets ----------------------------------
# Lives in a subdir of the layer-preview dir so a single cleanup of
# layer_preview/{file_id}/ removes both. Subdir keeps layout SVGs from
# colliding with layer SVGs (a layout could share a layer's name).
def layout_preview_dir(file_id: str) -> Path:
    return layer_preview_dir(file_id) / "layouts"


def layout_manifest_path(file_id: str) -> Path:
    return layout_preview_dir(file_id) / "layouts.json"


def layout_preview_svg_path(file_id: str, safe_name: str) -> Path:
    return layout_preview_dir(file_id) / f"{safe_name}.svg"
