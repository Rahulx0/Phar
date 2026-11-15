.DEFAULT_GOAL := help
SHELL := /bin/bash
HTTPS_PORT ?= $(shell awk -F= '/^NGINX_HTTPS_PORT=/{print $$2; found=1} END{if (!found) print 443}' .env 2>/dev/null)

.PHONY: help install lint format test up down logs ps restart-db backup restore certs demo-expired demo-oversell clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install project + dev tools
	python3 -m venv .venv && .venv/bin/pip install --upgrade pip \
		&& .venv/bin/pip install -e .[dev]

lint: ## Run ruff format check + lint
	.venv/bin/ruff format --check . && .venv/bin/ruff check .

format: ## Auto-format with ruff
	.venv/bin/ruff format . && .venv/bin/ruff check --fix .

test: ## Run pytest (against SQLite by default; Postgres if DATABASE_URL is set)
	.venv/bin/pytest -v

# --- Docker stack ----------------------------------------------------------

certs: ## Generate the self-signed TLS certificate for nginx
	bash deploy/generate-cert.sh

up: ## Build and start the whole stack (db + app + nginx with TLS)
	docker compose up -d --build

down: ## Stop the stack (data volume is kept)
	docker compose down

logs: ## Follow application logs (JSON lines)
	docker compose logs -f app

ps: ## Show stack status incl. health states
	docker compose ps

restart-db: ## FAILURE DRILL: kill the database to demo detection + recovery
	docker compose stop db

backup: ## Dump the database to ./backups/ (keeps newest 7)
	bash deploy/backup.sh

restore: ## Restore from a dump: make restore FILE=backups/<name>.dump
	@test -n "$(FILE)" || (echo "usage: make restore FILE=backups/pharmacy-<stamp>.dump"; exit 1)
	bash deploy/restore.sh "$(FILE)"

# --- Domain rule demos -----------------------------------------------------

demo-expired: ## Show a sale from the expired seed batch being rejected
	@echo ">>> Batches (id 2 is the expired Paracetamol batch):"
	curl -ksS 'https://localhost:$(HTTPS_PORT)/batches?expired=true' | python3 -m json.tool
	@echo ">>> Attempting to sell 5 units from the expired batch:"
	curl -ksS -X POST https://localhost:$(HTTPS_PORT)/sales -H 'Content-Type: application/json' \
		-d '{"batch_id": 2, "quantity": 5}' | python3 -m json.tool
	@echo ">>> Stock is unchanged and no SALE movement was recorded:"
	curl -ksS 'https://localhost:$(HTTPS_PORT)/movements?batch_id=2' | python3 -m json.tool

demo-oversell: ## Show 12 concurrent sales of 1 unit against the 8-unit Amoxicillin batch
	@echo ">>> Resetting demo batch 3 to 8 units (for repeatable runs)..."
	@curl -ksS -X POST https://localhost:$(HTTPS_PORT)/adjustments \
		-H 'Content-Type: application/json' \
		-d '{"batch_id": 3, "counted_quantity": 8, "note": "reset for concurrency demo"}' \
		> /dev/null
	@echo ">>> Batch 3 (Amoxicillin, 8 units) before:"
	curl -ksS https://localhost:$(HTTPS_PORT)/batches/3 ; echo
	@echo ">>> Firing 12 concurrent sale requests..."
	@seq 1 12 | xargs -P 12 -I{} curl -ksS -o /dev/null \
		-w '%{http_code}\n' -X POST https://localhost:$(HTTPS_PORT)/sales \
		-H 'Content-Type: application/json' -d '{"batch_id": 3, "quantity": 1}' \
		| sort | uniq -c
	@echo ">>> Batch after (exactly 8 x 201, 4 x 422, quantity exactly 0, never negative):"
	curl -ksS https://localhost:$(HTTPS_PORT)/batches/3 ; echo

clean: ## Remove local dev databases and caches
	rm -f pharmacy.db test_local.db
	rm -rf .pytest_cache .ruff_cache
