VENV := gateway/.venv/bin

.PHONY: install dev gateway web test test-py test-web secrets secrets-check secrets-doctor smoke-cma fmt

install:
	python3 -m venv gateway/.venv
	$(VENV)/pip install -q -e "gateway[dev]"
	cd web && pnpm install

dev:
	@echo "gateway :8100 + web :3000 — Ctrl-C stops both"
	@trap 'kill 0' INT TERM; \
	  ( $(VENV)/uvicorn tp_gateway.main:app --app-dir gateway --port 8100 --reload & \
	    cd web && pnpm dev & \
	    wait )

gateway:
	$(VENV)/uvicorn tp_gateway.main:app --app-dir gateway --port 8100 --reload

web:
	cd web && pnpm dev

test: test-py test-web

test-py:
	$(VENV)/pytest -q gateway/tests

test-web:
	cd web && pnpm exec tsc --noEmit && pnpm test

fmt:
	$(VENV)/ruff format gateway && $(VENV)/ruff check --fix gateway

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
