# Pharmacy Management Platform — Cloud Deployment & CI/CD

A small **Medicine, batch and stock-movement API** (the allocated service) made
consistently buildable, deployable, observable and recoverable with
production-style practices: containerized delivery, automated CI/CD, TLS-terminated
reverse proxy, health checks, structured logs, and tested backup/restore/rollback.

> Scope note: this is the DevOps assessment project. The domain API is
> intentionally minimal; the engineering weight is in build, release,
> deployment, reliability and operations.

## Architecture (30 seconds)

```
git push ──► GitHub Actions CI
               ├─ quality: ruff format/lint + pytest (real Postgres service)
               ├─ build:   multi-stage image + Trivy scan + push to GHCR (sha-… tag)
               └─ smoke:   boots the pushed image with Postgres, verifies /health /ready /medicines

Deployment host (docker compose):
   client ──HTTPS──► nginx :443 ──► app :8000 (non-root) ──SQL──► postgres :5432
                        │                                            │
                     self-signed                              pgdata volume
                     TLS certificate                        (pg_dump backups)
```

Full diagram and design decisions: [ARCHITECTURE.md](ARCHITECTURE.md) ·
Operations: [RUNBOOK.md](RUNBOOK.md)

## Quickstart (documented clean-environment path)

Prerequisites: Docker (with Compose v2), GNU make, openssl.

```bash
cp .env.example .env          # then set a real POSTGRES_PASSWORD
make certs                    # self-signed TLS certificate for nginx
make up                       # build + start db, app and nginx
curl -k https://localhost/health
# {"status":"ok","version":"local"}
curl -k https://localhost/ready  # {"status":"ready","database":"up"}
```

Open the interactive API docs: `https://localhost/docs` (accept the self-signed
cert warning). These URLs assume the default HTTPS port 443. If that port is
already in use, set `NGINX_HTTPS_PORT` in `.env` and append it to the URL (for
example, `https://localhost:8443/docs`). The verified local setup used HTTP
8080 and HTTPS 8443.

The database is seeded on first start with 5 medicines and 6 batches —
including an **expired batch**, a **near-expiry batch** and a **below-reorder
medicine** so every rule and report flag is demonstrable immediately.

## The API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness — process up (reports running version) |
| GET | `/ready` | readiness — database reachable (503 when down) |
| GET/POST | `/medicines` | catalogue list / create |
| GET | `/medicines/{id}` | single medicine |
| GET/POST | `/batches` | batch list (`?medicine_id=&expired=`) / create |
| GET | `/batches/{id}` | single batch |
| POST | `/purchases` | stock in (+qty) |
| POST | `/sales` | stock out (−qty) — enforces expiry + quantity rules |
| POST | `/returns` | customer return (+qty) |
| POST | `/adjustments` | cycle count (set to counted qty, records signed delta) |
| GET | `/movements` | audit trail (`?batch_id=&medicine_id=&limit=`) |
| GET | `/report/stock` | stock per medicine + expired / expiring-soon / reorder flags |

### Domain rules (acceptance-tested in `tests/test_domain_rules.py`)

- Negative or zero quantities → `422` (Pydantic validation, `quantity: gt=0`)
- Sale from an **expired batch** → `422 EXPIRED_BATCH`, stock unchanged, no movement recorded
- Sale exceeding batch quantity → `422 INSUFFICIENT_STOCK`, stock unchanged
- Concurrent sales of the same batch cannot oversell: the batch row is locked
  with `SELECT … FOR UPDATE` inside the sale transaction
- Every purchase/sale/return/adjustment writes an immutable `StockMovement`
  with the resulting `balance_after` — a complete audit trail
- A `CHECK (quantity >= 0)` constraint is the database-level backstop

Demo both rejection rules:

```bash
make demo-expired    # sells from the seeded expired batch → rejected, stock unchanged
make demo-oversell   # resets batch 3 to 8, then 8 succeed / 4 rejected (use HTTPS_PORT=8443 if needed)
```

## Environment variables & port map

| Variable | Default | Used by | Notes |
|---|---|---|---|
| `POSTGRES_USER` | `pharmacy` | compose (db) | |
| `POSTGRES_PASSWORD` | — **required** | compose (db + app) | compose refuses to start if unset |
| `POSTGRES_DB` | `pharmacy` | compose (db) | |
| `DATABASE_URL` | SQLite (dev) | app | full SQLAlchemy URL, e.g. `postgresql://user:pass@db:5432/pharmacy` |
| `APP_VERSION` | `dev` | app | injected from image tag; shown by `/health` |
| `LOG_LEVEL` | `INFO` | app | `DEBUG`/`INFO`/… |
| `IMAGE_NAME` / `IMAGE_TAG` | `pharmacy-api` / `local` | compose | which image the app service runs — **the rollback lever** |
| `NGINX_HTTP_PORT` / `NGINX_HTTPS_PORT` | `80` / `443` | compose | host ports; override if already in use (this workspace used `8080` / `8443`) |

| Port | Service | Exposure |
|---|---|---|
| 443 (default) | nginx | published — TLS entrypoint (`http://:80` redirects to HTTPS); set `NGINX_HTTPS_PORT` / `NGINX_HTTP_PORT` to override (verified locally on 8443 / 8080) |
| 8000 | app | internal; bound to `127.0.0.1:8000` on the host for debugging only |
| 5432 | postgres | internal only, never published |

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pushes to `main`, version tags, and pull requests:

1. **quality** — `ruff format --check`, `ruff check`, `pytest` against a real
   PostgreSQL *service container* (so row locking is genuinely exercised).
2. **build** — multi-stage Docker build → **Trivy** scan (fails on fixable
   HIGH/CRITICAL) → push to **GHCR** tagged `sha-<commit>` (+ `vX.Y.Z` on tags,
   `latest` on main). Pushes use the short-lived job-scoped `GITHUB_TOKEN` —
   no long-lived credentials in the pipeline.
3. **smoke** — deployment rehearsal: runs the *pushed* image next to a fresh
   Postgres, waits for `/health`, then verifies `/ready` and the seeded
   catalogue via `/medicines`.

Artifacts are immutable per commit (`sha-…` tags), which is what makes the
rollback procedure safe.

## Deploy / rollback (summary — full detail in RUNBOOK.md)

```bash
# deploy the version CI built for commit abc1234:
IMAGE_NAME=ghcr.io/rahulx0/phar IMAGE_TAG=sha-abc1234 \
  docker compose pull app && docker compose up -d app

# rollback to the previous known-good version (sha-oldone):
IMAGE_NAME=ghcr.io/rahulx0/phar IMAGE_TAG=sha-oldone \
  docker compose pull app && docker compose up -d app
curl -k https://localhost/health   # version field confirms which tag is live
```

## Backup & restore

```bash
make backup                                   # pg_dump -Fc → ./backups/ (keeps 7)
make restore FILE=backups/pharmacy-<stamp>.dump
```

Evidence walkthrough (drop → restore → verify): [RUNBOOK.md](RUNBOOK.md#backup--restore).

## Local development & tests

```bash
make install      # venv + editable install incl. dev tools
make lint         # ruff format check + lint
make test         # pytest (SQLite by default)
DATABASE_URL=postgresql://pharmacy:pharmacy@localhost:5432/pharmacy_test make test   # vs Postgres
```

## Security posture

- Multi-stage build; runtime image has no build toolchain; runs as non-root `app` user
- Secrets only via environment (`.env` is gitignored; only `.env.example` is committed)
- Least-privilege networking: only nginx publishes ports; DB is internal-only
- TLS 1.2/1.3 only, HTTP→HTTPS redirect, `server_tokens off`
- Trivy vulnerability gate in CI; base images use explicit version tags (tags are not digest-pinned)
- Database `CHECK` constraint as integrity backstop

## Known limitations / assumptions (stated per the brief)

- **Single-node compose deployment** — suitable for the local/VM target the
  brief allows; not a HA/Kubernetes setup. The compose file is the seam where
  a K8s manifest or ECS task definition would slot in.
- **Schema bootstrap via `create_all`** — fine at this size; Alembic migrations
  are the documented next step.
- **Self-signed certificate** — correct for a local/VM demo; a public
  deployment would use ACME/Let's Encrypt.
- **Metrics (stretch goal) not included** — observability currently covers
  health/readiness + structured JSON logs; Prometheus `/metrics` is the next addition.
- **Assumption:** sales are blocked from expired batches; purchases/returns on
  expired batches remain allowed so corrections stay auditable.
- The concurrency test is skipped on SQLite because SQLite ignores
  `SELECT … FOR UPDATE`; it runs against PostgreSQL in CI and was verified
  against the local Docker PostgreSQL stack.

## Submission evidence index

| Requirement | Where |
|---|---|
| Pipeline run URL | GitHub → Actions → CI (green run) |
| Image tag/digest | GHCR package page (`sha-<commit>` tag, digest on the run page) |
| Deployment + health evidence | RUNBOOK.md “Evidence capture” + demo video |
| Backup/restore evidence | RUNBOOK.md [Backup & restore](RUNBOOK.md#backup--restore) |
| Architecture diagram | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Test output | CI quality job (pytest, incl. expired-batch and oversell tests) |
