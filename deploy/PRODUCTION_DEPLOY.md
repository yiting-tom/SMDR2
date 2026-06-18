# 尋形 Conform — Production Deployment (Helm)

Production runbook for deploying Conform to the company Kubernetes cluster
with Helm. The chart lives at [`deploy/helm/conform`](helm/conform); it is a
faithful Helm port of [`deploy/k8s/conform.yaml`](k8s/conform.yaml).

> **One-line mental model:** the chart deploys only the *app* (stateless web
> replicas + a job worker). MariaDB, MinIO and Keycloak are **IT-managed**
> and reached purely by env var. Moving between environments = changing
> `values` + the secret, never code.

---

## 0. What the chart does (and does not) manage

| Managed by this chart | NOT managed (IT / external) |
|---|---|
| `conform-web` Deployment (×2, stateless) | MariaDB (app DB; schema is Alembic-owned) |
| `conform-worker` Deployment (×2) | MinIO / S3 (blob store) |
| `conform-config` ConfigMap | Keycloak realm + `conform-web` client |
| `conform-migrate` Job (Helm pre-upgrade hook) | The `conform-secrets` Secret (Vault / kubectl) |
| Service · Ingress · PodDisruptionBudget | TLS cert (company wildcard / cert-manager) |

Web and worker run the **same image**, different commands (web = uvicorn,
worker = `python -m app.worker_loop`, migrate = `alembic upgrade head`).

---

## 1. Prerequisites

- [ ] `kubectl` context points at the **prod** cluster; you can reach the `conform` namespace.
- [ ] `helm` ≥ 3.8 installed.
- [ ] **Image built and pushed** to the registry, tagged with an immutable **commit SHA** (CI does this on `main`). Never deploy `latest`.
- [ ] **MariaDB** reachable from the cluster; an empty database `conform` (utf8mb4) and a user exist. The migration Job creates all tables — do **not** hand-create schema.
- [ ] **MinIO/S3** reachable; the bucket (`conform`) exists; access key + secret issued.
- [ ] **Keycloak** realm imported; a confidential client `conform-web` exists with:
  - Valid redirect URI `https://<host>/auth/callback`
  - Valid **post-logout** redirect URI `https://<host>/*` (or `+`)
  - Client secret issued
  - The protocol mappers that emit `preferred_username`, `deptid`, `deptname`, … (see [`deploy/keycloak/realm-conform.json`](keycloak/realm-conform.json) for the exact claim set).

---

## 2. Create the secret (out of band — never in Git or values)

The app **fails fast at startup** if any of these five keys is missing in
`oidc` mode (`validate_startup_config`), so the rollout will not go healthy
until the secret is complete.

### Option A — Vault injection
Configure your Vault sidecar/CSI to project a Secret named **`conform-secrets`**
in the `conform` namespace with the five keys below.

### Option B — kubectl (manual / bootstrap)
```bash
kubectl create namespace conform   # idempotent; or let helm --create-namespace do it

kubectl -n conform create secret generic conform-secrets \
  --from-literal=DATABASE_URL='mysql+pymysql://USER:PASS@mariadb.prod.svc:3306/conform?charset=utf8mb4' \
  --from-literal=S3_ACCESS_KEY_ID='...' \
  --from-literal=S3_SECRET_ACCESS_KEY='...' \
  --from-literal=OIDC_CLIENT_SECRET='...' \
  --from-literal=SESSION_SECRET="$(openssl rand -hex 32)"
```

| Key | Notes |
|---|---|
| `DATABASE_URL` | Full SQLAlchemy URL, `mysql+pymysql://…?charset=utf8mb4`. **Or** split it — see "DB from parts" below. |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | MinIO credentials. |
| `OIDC_CLIENT_SECRET` | Keycloak `conform-web` client secret. |
| `SESSION_SECRET` | Signs the OIDC state cookie. **Generate once, keep stable** — rotating it invalidates in-flight logins. |

> `SESSION_SECRET` rotation is a deliberate, low-blast-radius event (only
> mid-login users are affected). Treat it as a real secret, store it in Vault.

### DB from parts (Vault holds only username + password)

If the Vault entry for the database is just a **username + password** (no
full URL), don't hand-build a `DATABASE_URL` — a password with `@ : / # ?`
breaks a naively concatenated URL. Instead put **`DB_USER` + `DB_PASSWORD`**
in the secret and the non-secret host/db in the ConfigMap; the app composes
the URL itself (password URL-encoded via SQLAlchemy `URL.create`):

```bash
# secret: just the two credentials (instead of DATABASE_URL)
kubectl -n conform create secret generic conform-secrets \
  --from-literal=DB_USER='conform' \
  --from-literal=DB_PASSWORD='...' \
  --from-literal=S3_ACCESS_KEY_ID='...' --from-literal=S3_SECRET_ACCESS_KEY='...' \
  --from-literal=OIDC_CLIENT_SECRET='...' \
  --from-literal=SESSION_SECRET="$(openssl rand -hex 32)"
```

ConfigMap (non-secret): `DB_HOST` (required), `DB_PORT` (default 3306),
`DB_NAME` (default `conform`), optionally `DB_DRIVERNAME` (default
`mysql+pymysql`) / `DB_CHARSET` (default `utf8mb4`).

**Precedence:** `DATABASE_URL` (if set) always wins; otherwise `DB_HOST` +
`DB_USER` trigger the composed URL; otherwise the app falls back to local
SQLite. So the two styles are mutually exclusive — pick one. The migration
Job and the web/worker pods resolve the URL identically.

---

## 3. Configure values

```bash
cd deploy/helm/conform
cp values-prod.example.yaml values-prod.yaml    # values-prod.yaml is .helmignore'd
$EDITOR values-prod.yaml
```

Must-set before the **first** rollout:

- `image.repository` + `image.tag` (commit SHA)
- `config.S3_ENDPOINT_URL`, `config.S3_BUCKET`
- `config.OIDC_ISSUER` (browser-facing), `config.OIDC_INTERNAL_BASE` (cluster-internal), `config.OIDC_CLIENT_ID`, `config.OIDC_REDIRECT_URI`
- `config.BOOTSTRAP_ADMINS` — **comma-separated userids. This is the only way the first admin is created.** Empty = nobody can administer.
- `ingress.host` (+ `ingress.tls` if this ingress terminates TLS)

> **OIDC dual-URL:** `OIDC_ISSUER` is what tokens carry in `iss` (the public
> ingress URL); `OIDC_INTERNAL_BASE` is how the backend reaches Keycloak
> in-cluster for token/JWKS calls. They are usually different hosts — set both.

---

## 4. First install

```bash
helm upgrade --install conform deploy/helm/conform \
  --namespace conform --create-namespace \
  -f deploy/helm/conform/values-prod.yaml \
  --atomic --timeout 10m
```

What happens, in order:
1. **pre-install hook** runs `conform-migrate` (`alembic upgrade head`) with the new image. If it fails, the release **aborts** — web never starts against an unmigrated DB.
2. ConfigMap, Service, Ingress, PDB applied.
3. `conform-web` ×2 and `conform-worker` ×2 roll out. `--atomic` rolls everything back if they don't go healthy within the timeout.

Always dry-run first when unsure:
```bash
helm template conform deploy/helm/conform -n conform -f deploy/helm/conform/values-prod.yaml | less
# or against the live cluster:
helm upgrade --install conform deploy/helm/conform -n conform -f deploy/helm/conform/values-prod.yaml --dry-run
```

---

## 5. Verify

```bash
# Migration succeeded
kubectl -n conform get job conform-migrate
kubectl -n conform logs job/conform-migrate

# Pods healthy (2 web + 2 worker)
kubectl -n conform get pods -o wide          # webs should land on different nodes
kubectl -n conform rollout status deploy/conform-web
kubectl -n conform rollout status deploy/conform-worker

# Health endpoint (auth-exempt)
kubectl -n conform exec deploy/conform-web -- wget -qO- http://localhost:8000/healthz

# End-to-end: log in via the ingress host, confirm a BOOTSTRAP_ADMINS user
# lands as admin, upload a small DXF, run a match + rule check.
```

---

## 6. Routine deploy (new image)

Each release is just a new SHA:

```bash
helm upgrade conform deploy/helm/conform \
  --namespace conform \
  -f deploy/helm/conform/values-prod.yaml \
  --set image.tag=<NEW_COMMIT_SHA> \
  --atomic --timeout 10m
```

- The **pre-upgrade hook** re-runs `alembic upgrade head` (idempotent at head) before pods roll.
- Web rollout is zero-downtime (`maxSurge 1 / maxUnavailable 0`, PDB `minAvailable 1`).
- Worker rollout (`maxSurge 0 / maxUnavailable 1`) never spins a 3rd 8Gi pod; an interrupted job requeues after the 120 s stale-claim window.
- Config-only changes (editing `values.config`) also trigger a web/worker restart via the pod-template `checksum/config` annotation.

---

## 7. Migrations

- Owned by Alembic (`alembic/versions/`, currently through `0007`). The chart never touches schema except via the hook Job.
- The hook uses `helm.sh/hook-delete-policy: before-hook-creation`, so the prior Job is removed before each run — no name collisions, and the logs of the latest attempt are always available until the next deploy.
- **Expand/contract rule:** during a rollout there is a brief window where old web pods run against the new schema. Keep migrations **backward-compatible** (additive columns/tables — as `0007` is). For a destructive change, split it across two releases: (1) add + dual-write, (2) drop after the old pods are gone.
- To skip the hook for a special case: `--set migrate.enabled=false` (then run the migration yourself out of band).

---

## 8. Rollback

```bash
helm history conform -n conform
helm rollback conform <REVISION> -n conform --wait --timeout 10m
```

⚠ Helm rolls back **manifests + image**, not the **database**. Only roll back
to a revision whose schema is compatible with the current DB. If a release
included a destructive migration, a code rollback alone is unsafe — that is
exactly why §7 mandates expand/contract.

---

## 9. Capacity & scaling

- **Replicas:** web 2, worker 2 — company policy floor (every Deployment ≥ 2). Raise web replicas for throughput freely.
- **Worker memory:** a 150 MB DXF peaks ≈ 6.3 GiB in one preprocess. Each worker requests 8 Gi with `SMDR2_MAX_WORKERS=1`. To run more concurrent preprocesses per worker, raise `worker.maxWorkers` **and** `worker.resources` **together** — never just the count.
- `scratch` is `emptyDir` (ephemeral per-pod working space) — sized ≥ source + derived artifacts. It is not shared state; all durable state is MariaDB + MinIO.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pods `CrashLoopBackOff`, log `Missing required configuration: …` | A `conform-secrets` key or required `config` var missing (fail-fast). | Complete the secret / values; `helm upgrade`. |
| Release aborts at the hook; `conform-migrate` failed | DB unreachable / bad `DATABASE_URL` / migration error. | `kubectl logs job/conform-migrate`; fix DB/creds; re-run `helm upgrade`. |
| Rollout hangs, web never Ready | Probe hitting an authed path, or app not up. | Probes use `/healthz` (auth-exempt) — confirm; check web logs. |
| Login loops / `iss` mismatch | `OIDC_ISSUER` ≠ the URL Keycloak mints, or redirect URI not registered. | Align `OIDC_ISSUER` with the public host; register `https://<host>/auth/callback` in Keycloak. |
| Logout returns to a logged-in app | Post-logout redirect URI not registered in Keycloak. | Register `https://<host>/*` (or `+`) as a valid post-logout redirect URI. |
| Uploads fail at ~200 MB | Ingress body cap. | `ingress.annotations` already sets `proxy-body-size: 200m`; keep it ≥ `SMDR2_MAX_UPLOAD_MB`. |
| Jobs stuck `queued` | No worker running. | Check `deploy/conform-worker`; webs set `SMDR2_EMBEDDED_WORKER=0` and only enqueue. |

---

## 11. Uninstall

```bash
helm uninstall conform -n conform
```

Removes chart-managed resources only. **`conform-secrets`, the MariaDB data
and the MinIO bucket survive** (not chart-managed) — delete those separately
and deliberately if you really intend to destroy the environment.
