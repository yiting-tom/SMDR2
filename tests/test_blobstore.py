"""LocalBlobStore contract + backend selection. The same contract runs
against MinIO in tests/test_minio_smoke.py (opt-in, compose)."""

from __future__ import annotations

import io

import pytest

from app.blobstore import LocalBlobStore, get_blobstore, reset_blobstore


@pytest.fixture
def store(tmp_path):
    return LocalBlobStore(tmp_path)


def test_bytes_text_json_roundtrip(store):
    store.put_bytes("a/b.bin", b"\x00\x01")
    assert store.get_bytes("a/b.bin") == b"\x00\x01"
    store.put_text("a/c.txt", "中文")
    assert store.get_text("a/c.txt") == "中文"
    store.put_json("a/d.json", {"k": [1, 2]})
    assert store.get_json("a/d.json") == {"k": [1, 2]}


def test_missing_key_raises_filenotfound(store):
    for op in (store.get_bytes, store.get_text, store.get_json,
               store.open_stream):
        with pytest.raises(FileNotFoundError):
            op("nope/missing")
    with pytest.raises(FileNotFoundError):
        with store.local_input("nope/missing"):
            pass


def test_exists_delete(store):
    assert not store.exists("x.json")
    store.put_text("x.json", "{}")
    assert store.exists("x.json")
    assert store.delete("x.json") is True
    assert store.delete("x.json") is False


def test_put_stream_and_open_stream(store):
    n = store.put_stream("up/f.dxf", io.BytesIO(b"dxfbytes"))
    assert n == 8
    with store.open_stream("up/f.dxf") as f:
        assert f.read() == b"dxfbytes"


def test_delete_many(store):
    store.put_text("pv/v1/f1/a.svg", "<svg/>")
    store.put_text("pv/v1/f1/layouts/b.svg", "<svg/>")
    store.put_text("pv/v1/f2/c.svg", "<svg/>")
    # missing keys are fine (blind delete) — count reflects real removals
    assert store.delete_many(
        ["pv/v1/f1/a.svg", "pv/v1/f1/layouts/b.svg", "pv/zzz/nope.svg"]
    ) == 2
    assert not store.exists("pv/v1/f1/a.svg")
    assert store.exists("pv/v1/f2/c.svg")
    assert store.delete_many([]) == 0


def test_no_list_operation():
    """Company MinIO forbids the list API — the interface must not grow
    one. Deletion enumerates keys from DB/manifests instead."""
    from app.blobstore import BlobStore, S3BlobStore
    for cls in (BlobStore, LocalBlobStore, S3BlobStore):
        assert not any("list" in name.lower() for name in vars(cls)), cls


def test_drop_stale_manifest_svgs(store):
    """Manifest rewrite deletes thumbnails the old manifest referenced
    but the new layer set no longer does (no-list bucket: anything missed
    here is unreachable garbage forever)."""
    from app.jobs import _drop_stale_manifest_svgs
    store.put_json("pv/layers.json",
                   {"layers": [{"safe_name": "keep"}, {"safe_name": "old"}]})
    store.put_text("pv/keep.svg", "<svg/>")
    store.put_text("pv/old.svg", "<svg/>")
    _drop_stale_manifest_svgs(
        store, "pv/layers.json", "layers", {"keep", "new"},
        lambda safe: f"pv/{safe}.svg",
    )
    assert store.exists("pv/keep.svg")
    assert not store.exists("pv/old.svg")
    # first run (no manifest yet) is a no-op
    _drop_stale_manifest_svgs(
        store, "pv/none.json", "layers", set(), lambda s: s,
    )


def test_local_input_yields_real_path(store):
    store.put_bytes("up/x.dxf", b"data")
    with store.local_input("up/x.dxf") as p:
        assert p.read_bytes() == b"data"
    # local backend: original survives the context (no scratch copy)
    assert store.exists("up/x.dxf")


def test_keys_map_to_current_layout(tmp_path):
    from app import storage
    s = LocalBlobStore(tmp_path)
    s.put_json(storage.parsed_key("v1", "f1"), {"ok": 1})
    assert (tmp_path / "parsed" / "v1" / "f1.json").exists()
    assert storage.key_of(storage.match_path("v9", "f9")) == \
        storage.match_key("v9", "f9")


def test_backend_selection(monkeypatch):
    reset_blobstore()
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    assert isinstance(get_blobstore(), LocalBlobStore)
    reset_blobstore()
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_BUCKET", "conform")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "s")
    from app.blobstore import S3BlobStore
    assert isinstance(get_blobstore(), S3BlobStore)
    reset_blobstore()


def test_s3_endpoint_without_credentials_fails_fast(monkeypatch):
    reset_blobstore()
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_BUCKET", "conform")
    monkeypatch.delenv("S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("S3_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="S3_ACCESS_KEY_ID"):
        get_blobstore()
    reset_blobstore()
