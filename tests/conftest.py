"""Shared pytest fixtures.

Most tests use isolated tmp directories for storage so they don't tread on
each other or on real `data/library.sqlite`. Tests that need the real
test.dxf use the `test_dxf_path` fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_dxf_path() -> Path:
    p = PROJECT_ROOT / "data" / "test.dxf"
    if not p.exists():
        pytest.skip(f"sample DXF not present at {p}")
    return p


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "library.sqlite"
