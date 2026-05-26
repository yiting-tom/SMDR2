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


@pytest.fixture(autouse=True)
def _clear_matching_caches():
    """Reset module-global matcher caches before every test.

    `_fingerprint_bucket_cache`, `_radius_bucket_cache`, and
    `_position_cluster_cache` in `app.matching` key on `id(drawing)`.
    Python re-uses `id()` after GC, so one test's `drawing` dict can
    collide with the next test's, causing the next test to read a
    stale bucket containing handles that don't exist in its own
    drawing — typically surfacing as `KeyError: '<handle>'` inside
    `_match_multi`.

    Until the cache scheme is reworked (weakref or content-hash key),
    clearing between tests removes the cross-test bleed at negligible
    cost.
    """
    from app.matching import (
        _fingerprint_bucket_cache,
        _position_cluster_cache,
        _radius_bucket_cache,
    )
    _fingerprint_bucket_cache.clear()
    _position_cluster_cache.clear()
    _radius_bucket_cache.clear()
    yield
