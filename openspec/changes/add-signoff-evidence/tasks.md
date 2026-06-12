# Tasks: add-signoff-evidence

## 1. Storage & schema

- [x] 1.1 `app/storage.py`:`sign_off_evidence_key(version_id)`
- [x] 1.2 `app/versions.py`:VERSIONS_SCHEMA +`evidence_name`/`evidence_type`;
      SQLite boot migration(比照 products._migrate_customer_id);
      `Version` dataclass / `to_dict` / `_row_to_version` 帶新欄;
      `sign_off(..., evidence_name=None, evidence_type=None)`;
      `unsign()` 清兩欄
- [x] 1.3 `alembic/versions/0005_signoff_evidence.py`(MariaDB)

## 2. API

- [x] 2.1 sign-off 端點:選填 `evidence: UploadFile`;magic-bytes 白名單
      (png/jpeg/webp)、10MB 上限、空檔視同未附;blob 先寫、`SignedOff` 409 時
      盲刪回滾;audit detail 帶 `evidence`
- [x] 2.2 `GET /api/versions/{vid}/sign-off/evidence`(viewer guard;404 無證明)
- [x] 2.3 unsign:清欄位 + 盲刪 blob
- [x] 2.4 product delete cascade:`_version_artifact_keys` 納入 evidence key

## 3. Frontend

- [x] 3.1 dashboard.js:簽核 confirm → 小 modal(確認文案 + 選填 file input);
      帶檔走 FormData multipart
- [x] 3.2 已簽核 badge 旁「📎 證明」連結(`evidence_name` 非 null 時)

## 4. Tests & docs

- [x] 4.1 tests:無檔案舊式呼叫不變、帶 PNG 成功(payload/audit/讀回 MIME)、
      偽造 Content-Type 但 magic 不符 → 415、>10MB → 413、unsign 清除、
      cascade 刪 blob、clone 不帶證明
- [x] 4.2 SYSTEM_DESIGN(§4 路由表、§7.4 生命週期)、CHANGELOG
