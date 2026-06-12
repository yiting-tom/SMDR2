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

| Phase | 內容 | 狀態 |
|---|---|---|
| 1 | SQLite→MariaDB(`DATABASE_URL`)、blob→MinIO(boto3,`S3_*`) | ✅ |
| 2 | jobs 表 + worker service、web 只 enqueue(`SMDR2_EMBEDDED_WORKER=0`) | ✅ |
| 3 | Keycloak BFF + 自建權限(`SMDR2_AUTH_MODE=oidc` 已開) | ✅ |

## k8s 部署([`deploy/k8s/conform.yaml`](k8s/conform.yaml))

單一 manifest,8 個資源,apply 順序已排好:

```
Namespace → ConfigMap → (Secret: Vault / kubectl create, 不進 repo)
→ Job conform-migrate(alembic upgrade head,冪等;CD 先 delete job 再 apply)
→ Deployment conform-web ×2(maxSurge 1 / maxUnavailable 0、anti-affinity 分節點、
   readiness+liveness 皆打 /healthz、SMDR2_EMBEDDED_WORKER=0)
→ PodDisruptionBudget(minAvailable 1 — drain 不會同時帶走兩台 web)
→ Deployment conform-worker ×2(政策:所有 Deployment ≥2;maxSurge 0 /
   maxUnavailable 1 — 滾動不出現第三個 8Gi pod;anti-affinity 分節點;
   無 HTTP 無 probe — 卡死由 120s stale-claim 協定自癒)
→ Service + Ingress(proxy-body-size 200m、read-timeout 300s、TLS 由公司側)
```

上線前要換的佔位:image registry、`*.example.internal` 三處、`BOOTSTRAP_ADMINS`。
Secret 五把(DATABASE_URL / S3 兩把 / OIDC_CLIENT_SECRET / SESSION_SECRET)缺一不可,
建法寫在 manifest 註解裡。oidc 切換演練:compose 即為 oidc 模式;裸跑/測試仍預設 bypass。

## CI/CD([`azure-pipelines.yml`](../azure-pipelines.yml))

`CI`(ruff+pytest 零依賴 ∥ MariaDB/MinIO smoke 用 docker 起真引擎)→ `Build`
(main 限定,push image,tag = commit SHA)→ `Deploy`(`conform-prod`
environment 掛 approval;sed 換 tag → 重跑 migration Job → apply → 等
migration complete → 等 web rollout)。要填的只有三個:`REGISTRY_SC`、
`IMAGE_REPO`、`K8S_SC`(說明在檔頭)。secrets 不經 pipeline。
