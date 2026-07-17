VENV := gateway/.venv/bin
WEB_REPO ?= ../talent-promo-web

.PHONY: install gateway test secrets secrets-check secrets-doctor smoke-cma fmt sync-fixtures

install:
	python3 -m venv gateway/.venv
	$(VENV)/pip install -q -e "gateway[dev]"

gateway:
	$(VENV)/uvicorn tp_gateway.main:app --app-dir gateway --port 8100 --reload

test:
	$(VENV)/pytest -q gateway/tests

fmt:
	$(VENV)/ruff format gateway && $(VENV)/ruff check --fix gateway

# Push regenerated golden fixtures + the canonical CONTRACT.md to the frontend repo
# (talent-promo-web vendors both — see its README). Run after any fold/mock/contract change.
sync-fixtures:
	@[ -d "$(WEB_REPO)" ] || { echo "frontend repo not found at $(WEB_REPO) (override WEB_REPO=...)"; exit 1; }
	cp gateway/tests/fixtures/mock_run.jsonl gateway/tests/fixtures/mock_run.snapshot.json \
	   gateway/tests/fixtures/mock_run_long.jsonl gateway/tests/fixtures/mock_run_long.snapshot.json \
	   "$(WEB_REPO)/test/fixtures/"
	cp CONTRACT.md "$(WEB_REPO)/CONTRACT.md"
	@echo "synced fixtures (both pairs) + CONTRACT.md -> $(WEB_REPO) (commit them there; run its golden test)"

# ── Secrets: 1Password references (op.env) → gitignored .env (reve pattern) ──
secrets: secrets-check
	@extras=$$( [ -f .env ] && grep -E '^(CMA_|TP_|NEXT_PUBLIC_)' .env || true ); \
	{ \
	  printf '# RENDERED by "make secrets" — CONTAINS REAL SECRETS. DO NOT COMMIT.\n'; \
	  printf '# .env is gitignored and disposable: delete anytime, regenerate from 1Password.\n\n'; \
	  sh scripts/render-secrets.sh op.env; \
	  if [ -n "$$extras" ]; then printf '\n# preserved from previous .env (setup.sh ids / local overrides)\n%s\n' "$$extras"; fi; \
	} > .env.tmp && mv .env.tmp .env || { rm -f .env.tmp; exit 1; }
	@echo 'secrets: rendered op.env -> .env (real secrets — gitignored, DO NOT COMMIT)'

secrets-check:
	@command -v op >/dev/null || { echo "1Password CLI 'op' not found — install it, then 'make secrets'"; exit 1; }
	@[ -n "$$OP_SERVICE_ACCOUNT_TOKEN" ] || op account get >/dev/null 2>&1 || { echo "no 1Password auth: set OP_SERVICE_ACCOUNT_TOKEN (container) or 'op signin' (laptop)"; exit 1; }

secrets-doctor: secrets-check
	@sh scripts/secrets-doctor.sh op.env

# P0 live smoke against real CMA (needs .env with ANTHROPIC_API_KEY + CMA_* ids)
smoke-cma:
	$(VENV)/python scripts/smoke_cma.py
