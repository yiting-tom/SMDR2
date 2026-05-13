"""Filesystem layout for SMDR2.

   data/
     uploads/{file_id}.dxf       — original DXF as uploaded by the user
     parsed/{file_id}.json       — cached flatten primitives + bbox + bg
     library.sqlite              — templates + file metadata
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
PREMATCH_DIR = DATA_DIR / "prematch"
MATCH_DIR = DATA_DIR / "match"
RULE_CHECK_DIR = DATA_DIR / "rule_check"
DB_PATH = DATA_DIR / "library.sqlite"

for d in (UPLOADS_DIR, PARSED_DIR, PREMATCH_DIR, MATCH_DIR, RULE_CHECK_DIR):
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
