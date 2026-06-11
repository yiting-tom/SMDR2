# Conform dev environment (docker-compose)

公司 k8s 環境的本地縮小鏡像。**所有環境差異收斂成 env var** — 搬上 k8s 時只換
config(prod secrets 走 Vault),code 不動。

```
nginx :8080 ──round-robin──> web-1 :8000
                          └> web-2 :8000     ← k8s 規定 2 pod,本地就用 2 replica 測
mariadb :3306   ← 公司由 IT 維運(3 replica);本地單節點,utf8mb4
minio   :9000   ← 公司由 IT 維運;一律走 boto3(S3 API)
keycloak:8081   ← 公司 IdP 替身;realm import 仿公司 JWT 自訂 claims
```

## 啟動

```bash
docker compose up --build
```

| 入口 | URL | 帳密 |
|---|---|---|
| App(必經 LB) | http://localhost:8080 | — |
| Keycloak admin | http://localhost:8081 | admin / admin |
| MinIO console | http://localhost:9001 | dev-access-key / dev-secret-key |
| MariaDB | localhost:3306 | conform / dev(db: conform) |

**一律從 :8080 進 app** — 直連 web-1/web-2 會繞過 LB,跨 replica 的 bug 就測不到。

## 測試帳號(Keycloak realm: conform,密碼皆 `dev`)

| 帳號 | deptid | 用途 |
|---|---|---|
| admin1 | D100 | 在 `BOOTSTRAP_ADMINS`,首登即 admin |
| editor1 | D100 | 與 admin1 同部門 |
| editor2 | D200 | 跨部門情境 |
| viewer1 | D200 | 預設無任何 grant |

JWT 透過 protocol mappers 帶出公司 token 的自訂 claims:`deptid`、`deptname`、
`company`、`twsitecode`、`supervisorid`、`location`、`description`(+ 標準
`preferred_username`、`name`、`email`)。`preferred_username` = userid。

OIDC 雙 URL 設計:`OIDC_ISSUER`(瀏覽器面向,token 的 iss)與
`OIDC_INTERNAL_BASE`(backend 網內呼叫 token/JWKS)— Keycloak 用
`KC_HOSTNAME` + `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` 讓兩者並存;k8s 上同樣是
ingress 公開 URL vs cluster 內 service URL 的對應。

## Phase 對應(詳見 memory/impl order)

| Phase | 內容 | compose 對應 |
|---|---|---|
| 1 | SQLite→MariaDB、blob→MinIO(boto3) | `DATABASE_URL`、`S3_*` 已備好 |
| 2 | jobs 表 + worker 迴圈、清 process cache | 解開 `worker` service 註解;移除共享 `appdata` volume |
| 3 | Keycloak BFF + 自建權限 | `OIDC_*`、`SESSION_SECRET`、`BOOTSTRAP_ADMINS` 已備好 |

目前 app 只消費 `SMDR2_*` 變數;其餘是 Phase 1–3 的 config contract,先把值
鋪好,各 phase 實作時直接取用。兩個 web 暫時共用 `appdata` volume(同一份
SQLite + data/),Phase 1 完成後該 volume 退化成 per-request scratch。
