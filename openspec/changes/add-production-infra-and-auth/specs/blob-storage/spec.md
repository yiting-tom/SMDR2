## ADDED Requirements

### Requirement: Blob access through a backend-neutral interface
All uploads and pipeline artifacts (uploads, parsed, prematch, match, rule_check, layer_preview) SHALL go through a `BlobStore` interface with two backends: S3 via boto3 (company MinIO; selected when `S3_ENDPOINT_URL` is set) and local filesystem (default — current behaviour for dev and tests). Object keys SHALL mirror the current relative path layout.

#### Scenario: Prod config selects S3
- **WHEN** `S3_ENDPOINT_URL`/`S3_BUCKET`/credentials are set
- **THEN** artifact reads and writes hit MinIO via boto3 and no derived artifact persists on pod-local disk

#### Scenario: Cross-replica artifact visibility
- **WHEN** web-1 accepts an upload and the worker (another pod) runs preprocess
- **THEN** the worker streams the DXF from the blob store and its outputs are readable from web-2

### Requirement: No bucket listing
The application SHALL NOT use the S3 list API (ListObjects/ListObjectsV2 — company MinIO rule, 2026-06-12). The `BlobStore` interface SHALL expose no list/iterate operation; every deletion SHALL enumerate its exact keys from the database (file bindings) or from the layer/layout manifests and go through `delete`/`delete_many` (batched DeleteObjects).

#### Scenario: Product delete cascade without listing
- **WHEN** an admin deletes a product
- **THEN** every version-scoped artifact key is computed from the version's file bindings plus the layer/layout manifests and removed via batched deletes, with no list request issued

#### Scenario: Manifest rewrite cleans stale thumbnails
- **WHEN** layer discovery re-runs and produces a different layer set
- **THEN** thumbnails recorded only in the previous manifest are deleted before the manifest is rewritten (nothing becomes unreachable garbage)

### Requirement: Streaming for large files
Reading and writing DXF blobs (up to 150MB+) SHALL stream to/from per-request scratch space rather than buffering whole objects in memory.

#### Scenario: 150MB DXF preprocess
- **WHEN** a 150MB DXF is preprocessed
- **THEN** the worker downloads it to scratch by streaming and cleans the scratch file afterwards

### Requirement: Upload size limit raised
The upload limit SHALL be configurable via `SMDR2_MAX_UPLOAD_MB` with a production value of at least 200 (SEC-001), enforced at both the proxy/ingress and the app.

#### Scenario: Oversized upload
- **WHEN** an upload exceeds the configured limit
- **THEN** it is rejected with 413 before reaching the parser
