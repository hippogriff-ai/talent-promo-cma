#!/bin/sh
# One-time CMA control-plane setup (spec §2.4). Idempotence: SKIPS any resource whose
# CMA_* id is already present in .env. Requires: `ant` CLI authed against the DEDICATED
# workspace (spec Appendix C #6) — `ant auth login --workspace-id ...` or the scoped
# ANTHROPIC_API_KEY exported (run `make secrets` first, then: set -a; . ./.env; set +a).
set -eu
cd "$(dirname "$0")/../.."
ENV_FILE=.env
touch "$ENV_FILE"

have() { grep -q "^$1=" "$ENV_FILE" 2>/dev/null && [ -n "$(grep "^$1=" "$ENV_FILE" | cut -d= -f2-)" ]; }
put() { printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; echo "  $1=$2"; }

command -v ant >/dev/null || { echo "ant CLI not found — brew install anthropics/tap/ant"; exit 1; }

echo "== environment =="
if have CMA_ENVIRONMENT_ID; then echo "  (skip: CMA_ENVIRONMENT_ID already in .env)"; else
  ENV_ID=$(ant beta:environments create < infra/cma/environment.yaml --transform id -r)
  put CMA_ENVIRONMENT_ID "$ENV_ID"
fi

echo "== agent =="
if have CMA_AGENT_ID; then echo "  (skip: CMA_AGENT_ID already in .env)"; else
  AGENT_JSON=$(ant beta:agents create < infra/cma/talent-promo-coach.agent.yaml --format json)
  AGENT_ID=$(printf '%s' "$AGENT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["id"])')
  AGENT_VERSION=$(printf '%s' "$AGENT_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["version"])')
  put CMA_AGENT_ID "$AGENT_ID"
  put CMA_AGENT_VERSION "$AGENT_VERSION"
fi

echo "== memory store =="
if have CMA_MEMORY_STORE_ID; then echo "  (skip: CMA_MEMORY_STORE_ID already in .env)"; else
  MEM_ID=$(ant beta:memory-stores create \
    --name "talent-promo owner" \
    --description "Career memory for the candidate: profile/master.md, profile/claims/* (one evidence-backed claim per file with provenance+status), qa/*, applications/<slug>/{research,gap-analysis,notes}.md, preferences.md. Read before asking; write as you learn." \
    --transform id -r)
  put CMA_MEMORY_STORE_ID "$MEM_ID"
fi

echo
echo "done. Optionally add CMA_WORKSPACE_ID=<wrkspc_...> to .env for correct Console links."
echo "Next: make dev  (engine picker offers 'cma'), or make smoke-cma for the P0 live smoke."
