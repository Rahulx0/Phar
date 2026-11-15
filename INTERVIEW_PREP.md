# Interview Prep — 15-minute walkthrough + 10-minute live change task

The brief says: *you must explain your own architecture and modify your own
work.* This file is the cheat sheet for both.

## 1. The 60-second architecture answer

> "A FastAPI service for medicines, batches and stock movements, deployed
> with docker-compose behind an nginx TLS proxy against PostgreSQL. GitHub
> Actions tests, scans and tags the image per commit to GHCR; deployment is
> pulling that tag. Observability is separate liveness/readiness endpoints
> and structured JSON logs. Recovery is documented and drilled: container
> restart policies for crashes, pre-ping pool for DB reconnection, pg_dump
> backups with a tested restore, and tag-based rollback."

## 2. Questions you should expect (with the answers your code actually gives)

**Q: Why FastAPI?**
Validation-heavy domain (Pydantic `gt=0` rules) + auto docs + tiny codebase.
The app is 5 marks; ops is 95 — I optimized for explainability.

**Q: How do you prevent overselling?** *(guaranteed question — know this cold)*
Inside the sale transaction: `SELECT … FOR UPDATE` locks the batch row, so
concurrent buyers serialise; each reads the freshest committed quantity,
checks, decrements, writes a movement, commits. The check happens *after*
acquiring the lock, so it can't see stale stock. `tests/test_domain_rules.py::
test_concurrent_sales_cannot_oversell` fires 10 threads against 5 units and
asserts exactly 5 succeed. It needs real row locks, so it runs against
Postgres (CI service container); SQLite ignores `FOR UPDATE`, which is why
it skips locally — that skip is documented in the test. The separate live demo
fires 12 requests against the seeded 8-unit batch.
Backstop: a DB-level `CHECK (quantity >= 0)` constraint.

**Q: Why is /ready separate from /health?**
Liveness ≠ readiness. `/health` says the process is up (no DB call, cheap,
always answers if the app can answer). `/ready` executes `SELECT 1` — it
flips to 503 when the DB is down. nginx/healthchecks route on readiness;
a restart triggers on liveness. Mixing them up restarts healthy-but-
databaseless containers forever or routes traffic into a dead app.

**Q: Why does the app survive the DB coming back without a restart?**
`pool_pre_ping=True`: SQLAlchemy pings a pooled connection before use and
transparently replaces dead ones. During the outage, `/ready` fails; after
recovery, the next request gets a fresh connection.

**Q: Walk me through your Dockerfile.**
Two stages. Builder: venv + `pip install .` with pip cache. Runtime:
`python:3.12-slim`, copies only `/opt/venv`, creates a non-root `app` user,
`USER app`, exposes 8000, `HEALTHCHECK` hits `/health`, no build tools, no
shell tools in PATH beyond slim. `.dockerignore` keeps tests, docs, `.env`
and backups out of the build context.

**Q: Why is the image tag a SHA and not just `latest`?**
`latest` is mutable — rollback means "some previous state nobody can name".
`sha-<commit>` is immutable and traceable to a CI run; `vX.Y.Z` tags mark
releases. Rollback = redeploy a known-good immutable tag.

**Q: What does CI do when Trivy finds a vulnerability?**
`exit-code: 1` with `ignore-unfixed: true` — the build job fails, nothing is
pushed. Fixable HIGH/CRITICAL block the release.

**Q: Why no long-lived credentials in CI?**
GHCR push uses the job-scoped `GITHUB_TOKEN` with `packages: write` granted
only to the build job. Nothing to rotate, nothing to leak.

**Q: Where are secrets?**
Only in environment variables. `.env` is gitignored (only `.env.example` is
committed), compose requires `POSTGRES_PASSWORD` to exist (`:?` syntax makes
the stack refuse to start otherwise), and the app reads config via
pydantic-settings.

**Q: Why is the restore done inside the running container instead of restoring the volume?**
`pg_restore` replaces the database inside the `pgdata` volume — the volume
itself stays mounted. The script first stops the app to release pooled DB
connections, drops and recreates the database, restores, then restarts and
verifies readiness. Recreating the volume from scratch is
documented too (`docker compose down -v` + recreate + restore) for the
from-scratch host rebuild case.

**Q: What are the known gaps?** *(say these before being asked)*
Alembic instead of `create_all`; Prometheus metrics (stretch goal);
single-node compose rather than HA/K8s — the app is stateless so a
Deployment+Service wraps it unchanged; self-signed cert because the target
is a local VM.

## 3. Live-change task (10 min): patterns to be fluent in

Open the repo in your editor beforehand and know these by file:

- **Add an endpoint** → `app/main.py` (route + `service` call), response
  schema in `app/schemas.py`. Then: `make lint && make test`.
- **Add a domain rule** (e.g. "sales capped at 100 units") → `app/service.py::sale`,
  raise a new `DomainError` subclass; the exception handler in `main.py`
  maps it to a status code automatically. Add a test next to the existing ones.
- **Add a config knob** → `app/config.py` (pydantic-settings) → read it via
  `settings`; if compose needs it, add to `docker-compose.yml` environment
  and `.env.example`.
- **Change a model** → `app/models.py` + delete the dev DB (`make clean`)
  since `create_all` doesn't migrate (say this out loud — it shows you know
  why Alembic is on the roadmap).

Any change you make live: run `ruff format . && ruff check . && pytest` —
the same gates CI enforces. Saying "CI would run exactly these" scores.

## 4. Numbers to have at your fingertips

- 14 API routes; 19 tests (18 pass locally on SQLite, 1 is skipped there; all 19 pass against PostgreSQL).
- Rubric mapping: containerization 20 → Dockerfile/compose; CI/CD 20 →
  ci.yml 3 jobs; deploy+security 15 → nginx TLS, non-root, internal DB;
  observability 15 → /health /ready + JSON logs; backup/rollback 15 →
  pg_dump drill + tag rollback; reliability/docs 10 → RUNBOOK; domain 5 →
  service.py rules
- Image tag scheme: `sha-<commit>`, `vX.Y.Z`, `latest` (main only)
- Backup retention: newest 7 dumps
