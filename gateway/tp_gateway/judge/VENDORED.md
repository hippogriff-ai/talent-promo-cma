# VENDORED from talent-promo

Source: `~/tech/talent-promo/apps/api/judge/` @ branch `gepa-prep` (untracked working tree,
copied 2026-07-13). Only change: imports rewritten `judge.*` → `tp_gateway.judge.*`.

- **Upstream owns prompt evolution.** The GEPA harness (`talent-promo/evals/`) exports new
  immutable prompt versions into upstream `judge/prompts/` and flips `ACTIVE_VERSION` there.
  To adopt a new version here: copy the new `prompts/<version>/` dir + update `prompts/ACTIVE_VERSION`,
  nothing else. Do not hand-edit prompt files in either repo.
- Keep `schemas.py` / `runner.py` / `spans.py` in sync with upstream if upstream changes —
  diff before editing locally. Judge contract (5 required string inputs) is load-bearing for
  the eval (spec §6.1, §7.5).
