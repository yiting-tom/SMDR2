"""Opt-in MinIO smoke — the LocalBlobStore contract against real S3 (the
compose MinIO), via boto3 as the company requires.

    SMDR2_MINIO_SMOKE=1 uv run pytest tests/test_minio_smoke.py -q

Uses the compose dev credentials/bucket unless S3_* env says otherwise.
"""

from __future__ import annotations

import io
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SMDR2_MINIO_SMOKE"),
    reason="SMDR2_MINIO_SMOKE not set",
)


@pytest.fixture
def store():
    from app.blobstore import S3BlobStore

    class Recording(S3BlobStore):
        """Track every key written so teardown can delete by exact key —
        the bucket has no list API (company rule), in tests too."""
        def __init__(self, **kw):
            super().__init__(**kw)
            self.written: set[str] = set()

        def put_bytes(self, key, data):
            self.written.add(key)
            super().put_bytes(key, data)

        def put_stream(self, key, fileobj):
            self.written.add(key)
            return super().put_stream(key, fileobj)

    s = Recording(
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:9000"),
        bucket=os.environ.get("S3_BUCKET", "conform"),
        access_key=os.environ.get("S3_ACCESS_KEY_ID", "dev-access-key"),
        secret_key=os.environ.get("S3_SECRET_ACCESS_KEY", "dev-secret-key"),
    )
    prefix = f"smoke/{uuid.uuid4().hex[:8]}"
    yield s, prefix
    s.delete_many(sorted(s.written))


def test_roundtrip_and_missing(store):
    s, pre = store
    s.put_json(f"{pre}/a.json", {"中文": 1})
    assert s.get_json(f"{pre}/a.json") == {"中文": 1}
    assert s.exists(f"{pre}/a.json")
    with pytest.raises(FileNotFoundError):
        s.get_bytes(f"{pre}/missing.json")
    assert not s.exists(f"{pre}/missing.json")


def test_stream_and_local_input(store):
    s, pre = store
    payload = b"x" * (5 * 1024 * 1024)  # multipart-ish but quick
    n = s.put_stream(f"{pre}/big.dxf", io.BytesIO(payload))
    assert n == len(payload)
    with s.open_stream(f"{pre}/big.dxf") as f:
        assert f.read(4) == b"xxxx"
    with s.local_input(f"{pre}/big.dxf") as p:
        scratch = str(p)
        assert p.stat().st_size == len(payload)
    assert not os.path.exists(scratch)  # scratch cleaned up


def test_delete_many(store):
    s, pre = store
    for i in range(3):
        s.put_text(f"{pre}/d/{i}.svg", "<svg/>")
    keys = [f"{pre}/d/{i}.svg" for i in range(3)]
    # blind batched delete: missing keys are fine, count = keys submitted
    assert s.delete_many(keys + [f"{pre}/d/missing.svg"]) == 4
    for k in keys:
        assert not s.exists(k)
