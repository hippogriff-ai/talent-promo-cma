# talent-promo-cma

Resume career-coach agent on **Claude Managed Agents (CMA)**, with an engine-agnostic UI.
You give it your resume + a dream-job posting; it researches, **interviews you to surface
experience you didn't realize was an asset**, drafts a grounded resume (judged against a
grounding judge — no fabrication), and exports. Spec: `docs/talent-promo-cma-spec.md`.
Wire contract both apps build against: `CONTRACT.md`.

## Quick start (zero keys — mock engine)

```sh
make install          # python venv + pip, pnpm install
make dev              # gateway :8100 + web :3000
# open http://localhost:3000 — start a run with engine "mock"
```

The mock engine replays a realistic coach run (plan, research, a real blocking question
you answer in the browser, a draft that fails judging, a revision that passes) over the
same wire protocol as CMA. No API keys, no cost.

## Real engine (CMA)

```sh
make secrets          # 1Password → .env  (op.env holds op:// refs only; see below)
make secrets-doctor   # which keys resolve (never prints values)
infra/cma/setup.sh    # once: create env + agent + memory store via `ant`; writes IDs to .env
make dev              # engine picker now offers "cma"
```

Requires: `ANTHROPIC_API_KEY` scoped to the **dedicated workspace** (spec Appendix C #6),
`OPENAI_API_KEY` for the grounding judge (absent → deterministic judge stub), `ant` CLI
authenticated. Judge module is vendored from talent-promo (`gateway/tp_gateway/judge/VENDORED.md`).

## Secrets (1Password, reve-style)

- `op.env` is **committed** and contains only `op://coding-agent/...` references.
- `make secrets` renders it to a **gitignored** `.env` via `op inject` (needs the `op` CLI
  signed in, or `OP_SERVICE_ACCOUNT_TOKEN`).
- `make secrets-doctor` reports ok/MISSING per reference without printing values.

## Evidence for talent-promo-eval

Every run — mock included — exports a full evidence bundle:
`GET /api/coach/runs/{id}/export` → event log, Q&A verbatim, drafts, judge inputs+verdicts,
plan history, usage (CONTRACT.md §7). Mock bundles double as eval-repo fixtures.

## Layout

```
CONTRACT.md      wire contract (source of truth for gateway + web)
gateway/         FastAPI + SQLite + engine adapters (mock, cma) + vendored judge
web/             Next.js UI (plan strip, activity feed, question cards, draft review)
infra/cma/       agent + environment YAML, ant-CLI setup script
docs/            spec (v2.2)
```
