"""Blob storage behind one interface (design D2, specs/blob-storage).

Two backends:

- `LocalBlobStore` (default): key → `DATA_DIR/key`. Byte-for-byte the
  current on-disk layout, so dev and the test suite keep their existing
  behaviour AND their existing path-based assertions.
- `S3BlobStore`: selected when `S3_ENDPOINT_URL` is set; company MinIO,
  boto3 only (company rule). Keys ARE the relative paths — no mental
  remapping between dev and prod.

Contract notes:
- Keys are POSIX-relative strings ("parsed/{vid}/{fid}.json"). The
  `*_key()` helpers in app.storage are the only place keys are minted.
- A missing key raises FileNotFoundError from every read op, on both
  backends — existing `except FileNotFoundError` call sites keep working.
- `local_input()` hands parsers (ezdxf needs a real file) a Path: the
  actual file on local, a streamed-down scratch copy on S3 (150MB DXFs
  must never be buffered whole in memory — design Risk #1).
- Workers run in separate processes: they resolve `get_blobstore()` on
  first use from env, which the parent's environment provides.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Protocol

from app.storage import DATA_DIR


class BlobStore(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def put_text(self, key: str, text: str) -> None: ...
    def get_text(self, key: str) -> str: ...
    def put_json(self, key: str, obj: Any, **dump_kw: Any) -> None: ...
    def get_json(self, key: str) -> Any: ...
    def put_stream(self, key: str, fileobj: BinaryIO) -> int: ...
    def open_stream(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def stat(self, key: str) -> str: ...  # opaque version token
    def local_input(self, key: str): ...  # contextmanager → Path


class LocalBlobStore:
    """Key → file under `root` (defaults to DATA_DIR). The current layout."""

    def __init__(self, root: Path | str = DATA_DIR) -> None:
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def put_text(self, key: str, text: str) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def get_text(self, key: str) -> str:
        return self._p(key).read_text()

    def put_json(self, key: str, obj: Any, **dump_kw: Any) -> None:
        self.put_text(key, json.dumps(obj, **dump_kw))

    def get_json(self, key: str) -> Any:
        return json.loads(self.get_text(key))

    def put_stream(self, key: str, fileobj: BinaryIO) -> int:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as out:
            shutil.copyfileobj(fileobj, out, length=1024 * 1024)
        return p.stat().st_size

    def open_stream(self, key: str) -> BinaryIO:
        return open(self._p(key), "rb")

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def delete(self, key: str) -> bool:
        try:
            self._p(key).unlink()
            return True
        except FileNotFoundError:
            return False

    def delete_prefix(self, prefix: str) -> int:
        base = self._p(prefix)
        if not base.exists():
            return 0
        n = sum(1 for f in base.rglob("*") if f.is_file())
        shutil.rmtree(base, ignore_errors=True)
        return n

    def stat(self, key: str) -> str:
        """Version token: mtime_ns (changes on rewrite). Raises
        FileNotFoundError on a missing key, like every read op."""
        return str(self._p(key).stat().st_mtime_ns)

    @contextmanager
    def local_input(self, key: str) -> Iterator[Path]:
        p = self._p(key)
        if not p.exists():
            raise FileNotFoundError(key)
        yield p


class S3BlobStore:
    """boto3-backed store against the company MinIO (S3 API)."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
        )

    def _missing(self, e: Exception, key: str) -> FileNotFoundError | None:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return FileNotFoundError(key)
        return None

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:
        from botocore.exceptions import ClientError
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if (fnf := self._missing(e, key)) is not None:
                raise fnf from e
            raise
        return resp["Body"].read()

    def put_text(self, key: str, text: str) -> None:
        self.put_bytes(key, text.encode("utf-8"))

    def get_text(self, key: str) -> str:
        return self.get_bytes(key).decode("utf-8")

    def put_json(self, key: str, obj: Any, **dump_kw: Any) -> None:
        self.put_text(key, json.dumps(obj, **dump_kw))

    def get_json(self, key: str) -> Any:
        return json.loads(self.get_text(key))

    def put_stream(self, key: str, fileobj: BinaryIO) -> int:
        # Multipart under the hood — constant memory for 150MB DXFs.
        self._client.upload_fileobj(fileobj, self.bucket, key)
        head = self._client.head_object(Bucket=self.bucket, Key=key)
        return int(head["ContentLength"])

    def open_stream(self, key: str) -> BinaryIO:
        from botocore.exceptions import ClientError
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if (fnf := self._missing(e, key)) is not None:
                raise fnf from e
            raise
        return resp["Body"]  # StreamingBody: file-like, chunked reads

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if self._missing(e, key) is not None:
                return False
            raise

    def delete(self, key: str) -> bool:
        existed = self.exists(key)
        self._client.delete_object(Bucket=self.bucket, Key=key)
        return existed

    def delete_prefix(self, prefix: str) -> int:
        if not prefix.endswith("/"):
            prefix += "/"
        paginator = self._client.get_paginator("list_objects_v2")
        n = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self._client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": keys}
                )
                n += len(keys)
        return n

    def stat(self, key: str) -> str:
        """Version token: the object ETag (changes on rewrite)."""
        from botocore.exceptions import ClientError
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if (fnf := self._missing(e, key)) is not None:
                raise fnf from e
            raise
        return head["ETag"]

    @contextmanager
    def local_input(self, key: str) -> Iterator[Path]:
        """Stream the object to per-request scratch, yield the path,
        always clean up. download_fileobj is chunked — no whole-object
        buffering."""
        from botocore.exceptions import ClientError

        suffix = Path(key).suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            try:
                self._client.download_fileobj(self.bucket, key, tmp)
            except ClientError as e:
                if (fnf := self._missing(e, key)) is not None:
                    raise fnf from e
                raise
            tmp.close()
            yield Path(tmp.name)
        finally:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass


_blobstore: BlobStore | None = None
_blobstore_lock = threading.Lock()


def get_blobstore() -> BlobStore:
    """Process-wide singleton. `S3_ENDPOINT_URL` set → S3, else local.
    Workers (separate processes) resolve this independently from the env
    they inherit."""
    global _blobstore
    with _blobstore_lock:
        if _blobstore is None:
            endpoint = os.environ.get("S3_ENDPOINT_URL")
            if endpoint:
                _blobstore = S3BlobStore(
                    endpoint_url=endpoint,
                    bucket=os.environ.get("S3_BUCKET", "conform"),
                    access_key=os.environ.get("S3_ACCESS_KEY_ID", ""),
                    secret_key=os.environ.get("S3_SECRET_ACCESS_KEY", ""),
                )
            else:
                _blobstore = LocalBlobStore()
        return _blobstore


def reset_blobstore() -> None:
    """Test hook — force re-resolution from env."""
    global _blobstore
    with _blobstore_lock:
        _blobstore = None
