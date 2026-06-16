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

## 正式部署:Helm(生產環境用這個)

生產走 Helm chart [`deploy/helm/conform`](helm/conform),完整步驟文件見
**[`deploy/PRODUCTION_DEPLOY.md`](PRODUCTION_DEPLOY.md)**。一行版:

```bash
helm upgrade --install conform deploy/helm/conform \
  -n conform --create-namespace \
  -f deploy/helm/conform/values-prod.yaml --atomic --timeout 10m
```

migration 走 Helm pre-upgrade hook(`alembic upgrade head`,失敗即中止 release);
secrets(5 把)走 Vault / kubectl,不進 chart。下面的原始 manifest 是這份 chart
的對照來源,保留作參考。

## k8s 原始 manifest(參考用,[`deploy/k8s/conform.yaml`](k8s/conform.yaml))

單一 manifest,8 個資源,apply 順序已排好:

```
Namespace → ConfigMap → (Secret: Vault / kubectl create, 不進 repo)
→ Job conform-migrate(alembic upgrade head,冪等;chart 以 pre-upgrade hook 跑,
   CD 走 Helm,不再 kubectl apply 這份 manifest)
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

公司用 **Azure Pipelines + Helm**。三 stage:

`CI`(ruff+pytest 零依賴 ∥ MariaDB/MinIO smoke 用 docker 起真引擎)→ `Build`
(main 限定,push image,tag = commit SHA)→ `Deploy`(`conform-prod`
environment 掛 approval)。

Deploy 就是 `helm upgrade --install conform deploy/helm/conform`:
`-f deploy/helm/conform/values-prod.yaml`、`--set image.repository/tag` 指向
Build 剛 push 的 image、`--atomic --timeout 10m`。`--atomic` 會先跑 alembic
**pre-upgrade hook**(migration),再滾 web/worker,任何一步沒健康就整包
rollback — migration+rollout 是一個 all-or-nothing 步驟,不再需要分開的
kubectl wait。

要填的只有三個 service connection:`REGISTRY_SC`、`IMAGE_REPO`、`K8S_SC`
(說明在 yml 檔頭)。`values-prod.yaml` **進 git**(只有 host/bucket/
BOOTSTRAP_ADMINS,無 secret;`.helmignore` 只是不打進 chart 包,不影響 git);
secrets 五把不經 pipeline(Vault → k8s Secret `conform-secrets`)。手動
`helm upgrade`(見 PRODUCTION_DEPLOY.md)是 break-glass / 首次 bootstrap 路徑,
跟 pipeline 跑的是同一條 chart。
