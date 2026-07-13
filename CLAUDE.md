# CLAUDE.md — talent-promo-cma

- **`CONTRACT.md` is the source of truth** for every wire shape, endpoint, fold rule, and env
  var. Change it deliberately, then make code match. The spec (`docs/talent-promo-cma-spec.md`)
  carries the rationale; owner rulings live in its Appendix C.
- Scope is **CMA only**. reve appears in the spec as context/design-target; do not build reve
  integration here.
- The **mock engine must always work with zero keys** — it is the local dev loop, the fixture
  source for the golden fold test, and the eval-repo's free fixtures. Don't add key requirements
  to it, and keep `gateway/tests/fixtures/mock_run.jsonl` in sync with `engines/mock.py`.
- The **golden fold test** pins the Python fold and the TS fold to identical snapshots. If you
  touch either fold or the fixture, regenerate and re-run both sides.
- `gateway/tp_gateway/judge/` is **vendored** from talent-promo — see its `VENDORED.md` before
  editing (upstream sync policy; GEPA exports new prompt versions upstream).
- Secrets: never commit `.env`; `op.env` holds only `op://` references; `make secrets` renders.
- Owner house rules: **no git commits/pushes 9am–5pm EDT** (file edits fine — queue commits);
  every phase ends with a **live smoke**, not just green units; models decide *content*, never
  *control flow*.
- Python: repo venv at `gateway/.venv` (`make install`). Web: pnpm. Run tests:
  `make test` (pytest + vitest + tsc).
