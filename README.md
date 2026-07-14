# talent-promo-cma

**Backend** for the talent-promo coach: Claude Managed Agents (CMA) engine behind a
contract-stable gateway. The frontend lives in the separate **`talent-promo-web`** repo —
it speaks only `CONTRACT.md` (canonical here), so backends are swappable under it.
You give it your resume + a dream-job posting; it researches, **interviews you to surface
experience you didn't realize was an asset**, drafts a grounded resume (judged against a
grounding judge — no fabrication), and exports. Spec: `docs/talent-promo-cma-spec.md`.
Wire contract both apps build against: `CONTRACT.md`.

## Quick start (zero keys — mock engine)

```sh
make install          # python venv + pip
make gateway          # :8100
# frontend: cd ../talent-promo-web && pnpm dev  (:3000) — start a run with engine "mock"
```

The mock engine replays a realistic coach run (plan, research, a real blocking question
you answer in the browser, a draft that fails judging, a revision that passes) over the
same wire protocol as CMA. No API keys, no cost.

## Real engine (CMA)

```sh
make secrets          # 1Password → .env  (op.env holds op:// refs only; see below)
make secrets-doctor   # which keys resolve (never prints values)
infra/cma/setup.sh    # once: create env + agent + memory store via `ant`; writes IDs to .env
make gateway          # engine picker (frontend) now offers "cma"
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
CONTRACT.md      wire contract — CANONICAL (frontend repo vendors a synced copy)
gateway/         FastAPI + SQLite + engine adapters (mock, cma) + vendored judge
infra/cma/       agent + environment YAML, ant-CLI setup script
docs/            spec (v2.2)
```

After any fold/mock/contract change: `make sync-fixtures` pushes regenerated golden
fixtures + CONTRACT.md to `../talent-promo-web` (commit them there).
