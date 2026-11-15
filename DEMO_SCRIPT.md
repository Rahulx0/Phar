# Demo Script (3–5 minutes) — Pharmacy Management Platform

Record at 1080p, terminal font ≥ 16pt. Every step below produces the evidence
the rubric asks for. Total runtime at a relaxed pace: ~4 minutes.

## 0. Open on the repo (15s)

Show the file tree and say:

> "This is the Pharmacy Management Platform assessment. The domain API is
> deliberately small — the engineering weight is in build, deployment,
> observability and recovery. The full local deployment and operations demo can be repeated from this repo."

Point at: `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`,
`RUNBOOK.md`.

## 1. CI pipeline (45s)

Open the GitHub Actions run page (green CI run for the final commit).

> "Every push runs three jobs. *Quality*: ruff format+lint and pytest — the
> tests run against a real PostgreSQL service container, not SQLite.
> *Build*: a multi-stage image build, scanned by Trivy — the pipeline fails
> on fixable HIGH/CRITICAL vulnerabilities — then pushed to GHCR tagged with
> the commit SHA, so artifacts are immutable. *Smoke*: the pipeline boots the
> image it just pushed next to a fresh Postgres and verifies /health, /ready
> and the seeded catalogue — a deployment rehearsal before anyone deploys."

Screenshot the run + the GHCR tag (`sha-<commit>`) → **pipeline URL + image
tag evidence**.

## 2. Clean deployment (30s)

Terminal, repo root:

```bash
cp .env.example .env   # set password once
make certs && make up
docker compose ps      # db, app, nginx all (healthy)
```

> "One command brings up the full stack: Postgres with a persistent volume,
> the API as a non-root container, nginx terminating TLS. Compose gates the
> start order with health checks — nginx never gets traffic before the app
> is ready. Only nginx publishes ports; the database is internal-only."

## 3. Health, readiness, logs (30s)

```bash
curl -k https://localhost:8443/health   # {"status":"ok","version":"sha-…"}
curl -k https://localhost:8443/ready    # {"status":"ready","database":"up"}
docker compose logs -f app        # JSON log lines
```

> "Liveness and readiness are separate: /health is process-up, /ready checks
> the database. Every request logs one structured JSON line with a request id
> and duration — the same id comes back as the X-Request-ID header."

## 4. Domain rules (45s)

```bash
HTTPS_PORT=8443 make demo-expired    # sale from expired seed batch → 422 EXPIRED_BATCH,
                                     # stock unchanged, no movement recorded
HTTPS_PORT=8443 make demo-oversell   # reset to 8, then exactly 8 x 201 + 4 x 422
```

> "The brief's acceptance rule: a sale against an expired batch must fail
> without changing stock. The response is machine-readable
> (`{"code":"EXPIRED_BATCH"}`), the quantity is untouched, and — this is the
> part I care about — no movement is written to the audit trail."

## 5. Failure & recovery drill (60s) — the money shot

```bash
# break it
docker compose stop db
curl -k https://localhost:8443/health   # 200 — process alive
curl -k https://localhost:8443/ready    # 503 — not ready, DB down
docker compose ps                  # app shows (unhealthy)

# recover
docker compose start db
sleep 10
curl -k https://localhost:8443/ready    # back to ready, app restarted itself
```

> "I break the database. Detection: /health stays 200 because the process is
> alive, but /ready flips to 503 and the container goes unhealthy — that's
> the difference between liveness and readiness, and it's what lets the
> orchestrator route traffic correctly. Recovery: the app reconnects through
> its pooled connections with pre-ping — no restart needed. The compose
> restart policy also self-heals a killed container."

## 6. Backup & restore (45s)

```bash
make backup                                  # pg_dump -Fc → backups/<stamp>.dump
# damage: drop the tables
docker compose exec db psql -U pharmacy -d pharmacy \
  -c 'DROP TABLE stock_movements, batches, medicines CASCADE;'
curl -k https://localhost:8443/report/stock       # [] — gone
make restore FILE=backups/pharmacy-<stamp>.dump
curl -k https://localhost:8443/report/stock       # restored, data verified
```

> "Backups are compressed pg_dump custom-format files with 7-copy retention.
> The restore drill proves the loop end to end: backup, destroy data,
> restore, verify — including that the audit trail came back."

## 7. Rollback (30s)

```bash
curl -k https://localhost:8443/health        # version = current tag
IMAGE_NAME=ghcr.io/rahulx0/phar IMAGE_TAG=sha-<previous-commit> \
  docker compose pull app && docker compose up -d app
curl -k https://localhost:8443/health        # version = previous tag
```

> "Because every commit produces an immutable sha-tagged image, rollback is
> just redeploying the previous tag — one env var, verified by the version
> field in /health."

## 8. Close (10s)

> "Repeatable deployment, CI with a vulnerability gate,
> least-privilege deployment, structured observability, and proven backup,
> restore and rollback — all from one repo, per the runbook."
