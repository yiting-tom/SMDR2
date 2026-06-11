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


def test_delete_prefix(store):
    store.put_text("pv/v1/f1/a.svg", "<svg/>")
    store.put_text("pv/v1/f1/layouts/b.svg", "<svg/>")
    store.put_text("pv/v1/f2/c.svg", "<svg/>")
    assert store.delete_prefix("pv/v1/f1") == 2
    assert not store.exists("pv/v1/f1/a.svg")
    assert store.exists("pv/v1/f2/c.svg")
    assert store.delete_prefix("pv/zzz") == 0


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
    from app.blobstore import S3BlobStore
    assert isinstance(get_blobstore(), S3BlobStore)
    reset_blobstore()
