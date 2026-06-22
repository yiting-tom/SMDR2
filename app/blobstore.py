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
- NO ListObjects, ever (company MinIO rule). There is deliberately no
  list/iterate operation on this interface — every deletion enumerates
  its exact keys from the DB (file bindings) or from the layer/layout
  manifests, then goes through `delete`/`delete_many`.
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
import logging
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator, Protocol

from app.storage import DATA_DIR

logger = logging.getLogger(__name__)


class BlobStore(Protocol):
    def ping(self) -> None: ...  # connectivity probe; raises if unreachable
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
    def delete_many(self, keys: Iterable[str]) -> int: ...
    def stat(self, key: str) -> str: ...  # opaque version token
    def local_input(self, key: str): ...  # contextmanager → Path


class LocalBlobStore:
    """Key → file under `root` (defaults to DATA_DIR). The current layout."""

    def __init__(self, root: Path | str = DATA_DIR) -> None:
        self.root = Path(root)

    def ping(self) -> None:
        """Connectivity probe: the root dir is creatable and writable."""
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".readyz"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)

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

    def delete_many(self, keys: Iterable[str]) -> int:
        n = 0
        for key in keys:
            if self.delete(key):
                n += 1
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

        from app.tlsconfig import ssl_verify

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4"),
            # verify follows SSL_VERIFY; only bites when endpoint is https://
            # (a plain http:// MinIO does no TLS regardless).
            verify=ssl_verify(),
        )

    def ping(self) -> None:
        """Connectivity probe: the bucket is reachable + we can see it."""
        self._client.head_bucket(Bucket=self.bucket)

    def _missing(self, e: Exception, key: str) -> FileNotFoundError | None:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return FileNotFoundError(key)
        # A genuine S3 error (auth, network, bucket policy …) — the caller will
        # re-raise it; log it here so the opaque boto traceback gains context.
        logger.warning("S3 error code=%r for key=%s (re-raising)", code, key)
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
        """Blind delete — S3 delete_object succeeds on missing keys and
        callers ignore the bool, so skip the extra HEAD round-trip."""
        self._client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def delete_many(self, keys: Iterable[str]) -> int:
        """Batched blind delete (DeleteObjects, ≤1000/request — the S3
        cap). No listing, no per-key HEAD: missing keys delete "fine", and
        callers don't act on the count, so it reports keys submitted."""
        batch = [{"Key": k} for k in keys]
        n = 0
        for i in range(0, len(batch), 1000):
            chunk = batch[i:i + 1000]
            self._client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": chunk, "Quiet": True}
            )
            n += len(chunk)
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
                # Worker pods resolve this without running the web lifespan,
                # so re-check the required group here too: a half-set S3 env
                # would otherwise init with empty creds / a default bucket and
                # only fail on first upload. No silent defaults.
                bucket = os.environ.get("S3_BUCKET", "")
                access_key = os.environ.get("S3_ACCESS_KEY_ID", "")
                secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "")
                missing = [
                    name for name, val in (
                        ("S3_BUCKET", bucket),
                        ("S3_ACCESS_KEY_ID", access_key),
                        ("S3_SECRET_ACCESS_KEY", secret_key),
                    ) if not val
                ]
                if missing:
                    raise RuntimeError(
                        "S3_ENDPOINT_URL is set but required vars are missing: "
                        + ", ".join(missing)
                    )
                _blobstore = S3BlobStore(
                    endpoint_url=endpoint,
                    bucket=bucket,
                    access_key=access_key,
                    secret_key=secret_key,
                )
                logger.info("blob store: S3 endpoint=%s bucket=%s",
                            endpoint, bucket)
            else:
                _blobstore = LocalBlobStore()
                # The worst silent prod fallback: per-pod local disk diverges
                # across replicas ("files vanish between pods"). Be loud.
                logger.warning(
                    "blob store: S3_ENDPOINT_URL unset — using local disk "
                    "(%s); NOT safe for multi-replica", DATA_DIR,
                )
        return _blobstore


def reset_blobstore() -> None:
    """Test hook — force re-resolution from env."""
    global _blobstore
    with _blobstore_lock:
        _blobstore = None
