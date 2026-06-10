"""Wipe the dev data tree: SQLite DB plus every derived-artifact dir.

Usage:
    uv run python scripts/reset_dev_data.py [--yes]

Honours SMDR2_DATA_DIR. Keeps loose files at the data/ root (e.g. the
sample test.dxf) — only the DB and the known artifact subdirs go.
"""

from __future__ import annotations

import shutil
import sys

from app import storage

ARTIFACT_DIRS = (
    storage.UPLOADS_DIR,
    storage.PARSED_DIR,
    storage.PREMATCH_DIR,
    storage.MATCH_DIR,
    storage.RULE_CHECK_DIR,
    storage.LAYER_PREVIEW_DIR,
)


def main() -> None:
    if "--yes" not in sys.argv:
        answer = input(f"Wipe {storage.DB_PATH} and artifact dirs under {storage.DATA_DIR}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("aborted")
            return
    for suffix in ("", "-wal", "-shm"):
        p = storage.DB_PATH.with_name(storage.DB_PATH.name + suffix)
        if p.exists():
            p.unlink()
            print(f"removed {p}")
    for d in ARTIFACT_DIRS:
        if d.exists():
            shutil.rmtree(d)
            print(f"removed {d}/")
        d.mkdir(parents=True, exist_ok=True)
    print("dev data reset.")


if __name__ == "__main__":
    main()
