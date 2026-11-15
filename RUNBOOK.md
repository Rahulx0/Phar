# Operations Runbook — Pharmacy Management Platform

Target audience: the engineer on call for this service (or the reviewer
reproducing it). Everything here assumes the repo root as the working directory.

## 1. Deploy

```bash
cp .env.example .env        # set POSTGRES_PASSWORD (required)
make certs                  # TLS certificate for nginx (once per host)
make up                     # build + start db → app → nginx
docker compose ps           # all three services should report (healthy)
curl -k https://localhost/health
curl -k https://localhost/ready
# If .env overrides the HTTPS port (e.g. 8443), use https://localhost:8443 instead.
```

Ordering is enforced by compose: the app waits for `db: service_healthy`,
nginx waits for `app: service_healthy`. No start-order race conditions.

**Deploying a CI-built image** (instead of the local build):

```bash
IMAGE_NAME=ghcr.io/rahulx0/phar IMAGE_TAG=sha-<commit> \
  docker compose pull app && docker compose up -d app
```

## 2. Logs

```bash
docker compose logs -f app            # JSON lines: request, stock.*, startup events
docker compose logs -f nginx db       # proxy + database logs
docker compose logs app | grep '"event": "stock.sale"'
docker compose logs app | grep '"request_id": "abc123"'   # follow one request
```

Every line is one JSON object with `timestamp`, `level`, `event`,
`request_id`, and for requests also `method`, `path`, `status_code`,
`duration_ms`. The same `X-Request-ID` is returned as an HTTP response header,
so a user-reported response can be traced to its log lines.

## 3. Restart / recovery

| Situation | Command |
|---|---|
| Restart the app only | `docker compose restart app` |
| Restart the whole stack | `docker compose restart` |
| Rebuild after code change | `make up` (rebuilds and recreates app) |
| Container died unexpectedly | `restart: unless-stopped` pulls it back up automatically; check `docker compose ps` then logs |

## 4. Failure drills (detection + recovery evidence)

### Drill A — database outage

```bash
docker compose stop db                          # "break" the database
docker compose ps                               # app turns (unhealthy) — /ready is failing
curl -k https://localhost/health                # 200: process is alive (liveness ≠ readiness)
curl -k https://localhost/ready                 # 503: not ready — DB unreachable
docker compose logs app | grep not_ready        # JSON error lines with tracebacks
docker compose start db                         # recovery
sleep 10 && docker compose ps                   # app returns to (healthy)
curl -k https://localhost/ready                 # {"status":"ready","database":"up"}
```

Why the app survives: DB connections are pooled with `pool_pre_ping=True`,
so dead connections are replaced transparently once the database returns.
No app restart is needed. This is the brief's "deliberately stop or break one
service and demonstrate detection/recovery".

### Drill B — application container crash

```bash
docker kill --signal=9 "$(docker compose ps -q app)"
docker compose ps           # container restarting → healthy again (restart policy)
curl -k https://localhost/health
```

## 5. Backup & restore

```bash
make backup                                    # pg_dump -Fc → ./backups/<timestamp>.dump
ls -lh backups/                                # newest 7 are retained automatically
# If using a non-default local HTTPS port, set HTTPS_PORT=8443 for Makefile demos;
# use https://localhost:8443 in the curl commands below.
```

### Full restore drill (with evidence capture)

```bash
# 0. state before: note current data
curl -ksS https://localhost/report/stock | python3 -m json.tool | head

# 1. make a change you will later "lose" (proves the restore really restores)
curl -ksS -X POST https://localhost/sales -H 'Content-Type: application/json' \
     -d '{"batch_id": 1, "quantity": 3, "note": "sale to be rolled back via restore"}'

# 2. backup
make backup          # → backups/pharmacy-<stamp>.dump   (screenshot this)

# 3. simulate disaster: destroy the data (drop tables inside the volume)
docker compose exec db psql -U pharmacy -d pharmacy -c 'DROP TABLE stock_movements, batches, medicines CASCADE;'

curl -ksS https://localhost/report/stock       # [] — data is gone

# 4. restore
make restore FILE=backups/pharmacy-<stamp>.dump

# 5. verify: catalogue and stock are back, including the "lost" sale
curl -ksS https://localhost/report/stock | python3 -m json.tool | head
docker compose exec db psql -U pharmacy -d pharmacy -c \
  'SELECT movement_type, quantity, balance_after, note FROM stock_movements ORDER BY id DESC LIMIT 3;'
```

The `pgdata` volume itself is never removed by the restore — only the
database inside it is replaced. For a from-scratch host rebuild:
`docker compose down` (keep volume) or `docker compose down -v` (destroy data
— only together with a backup drill).

## 6. Rollback procedure

Images are immutable and tagged per commit (`sha-…`, plus `vX.Y.Z` on release
tags), so rollback is redeploying the previous tag:

```bash
# 1. identify the currently running version
curl -k https://localhost/health        # "version": "sha-abc1234"

# 2. pick the previous known-good tag (CI run page / GHCR package tags)

# 3. redeploy it
IMAGE_NAME=ghcr.io/rahulx0/phar IMAGE_TAG=sha-old1234 \
  docker compose pull app && docker compose up -d app

# 4. verify
curl -k https://localhost/health        # "version": "sha-old1234"
curl -k https://localhost/ready
docker compose ps
```

Database rollback note: if a bad version wrote data, restore the pre-deploy
backup with `make restore FILE=…` (step 5 above). Schema changes should be
additive so old images keep running against the newer schema.

## 7. Incident response quick reference

| Symptom | Likely cause | First action |
|---|---|---|
| `curl /ready` → 503 | DB down or unreachable | `docker compose ps`, Drill A recovery |
| nginx 502/504 | app unhealthy/wedged | `docker compose ps`, `docker compose logs app`, restart app |
| Everything unhealthy after host reboot | stack not started | `make up`, then `docker compose ps` |
| Sales failing with `INSUFFICIENT_STOCK` unexpectedly | stock genuinely low or concurrent drain | `curl -k https://localhost/report/stock`, check movements audit trail |
| `EXPIRED_BATCH` on a sale | batch expiry date passed | intended behaviour — check `/batches?expired=true`, restock via a new batch |
| Cert warning in browser | self-signed cert (expected locally) | accept, or install `deploy/certs/server.crt` as a trusted CA on the demo machine |

## 8. Evidence capture (for the submission form)

- **Pipeline run URL:** GitHub → Actions → CI → the green run for the final commit
- **Image tag/digest:** shown on the CI build job (and the GHCR package page)
- **Deployment/health evidence:** terminal output of `docker compose ps` + the two `curl` calls in §1
- **Backup/restore evidence:** terminal transcript of §5 (steps 2–5)
- **Failure/recovery evidence:** terminal transcript of §4 Drill A
