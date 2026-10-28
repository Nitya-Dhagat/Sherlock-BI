# Implementation Status

_Last updated: 2026-10-28 (session 2)_

**Stage:** partial implementation. The analytic layer and integration layer work and are tested. The
UI is with a teammate. The agent loop works on Gemini. Not production-ready. Details: `PROJECT_AUDIT.md`. Plan: `IMPLEMENTATION_ROADMAP.md`.

## Completed (this session)
- Relocated the untracked WIP modules to their documented homes, contents unchanged. They failed to import before the move.
  `src/agent/{runner,confidence,validator}.py`, `src/tools/significance_test.py`
- Added `tests/test_agent.py`: confidence formula by hand, contract validity, edge cases; validator rounding,
  units, citations and hypotheses; runner error guards and evidence numbering. None of these need a database.
- Added `tests/test_significance.py`: schema, reproducibility, structured errors, and recovery of the scenario plant.
- Repo hygiene: fixed `.gitignore`; untracked `__pycache__`, `.DS_Store` and `tempCodeRunnerFile.py` (local files kept);
  added `requirements.txt` (adds `statsmodels`, which was missing).
- Docs: `README.md`, `CLAUDE.md`, `docs/PROJECT_AUDIT.md`, `docs/IMPLEMENTATION_ROADMAP.md`, this file.

## Completed (session 2: agent loop)
- `src/agent/llm.py`: provider adapter. Gemini (`google-genai`) is implemented with timeout, retries, and a model
  fallback chain; thought signatures are preserved because the model's own content is kept in the history.
- `src/agent/loop.py`: `run_investigation()` event generator, `submit_report`, contract and validator check with one
  retry, a tool-call limit, a nudge on text-only replies, and code-computed confidence. CLI with `--save` writes the trace.
- `src/agent/prompts/system_v1.md`, `.env.example`, `tests/test_loop.py` (12 fake-client tests).
- Live runs on the scenario DB (Gemini):
  - The demo question used 6 tool calls in playbook order, the report was validated on the first attempt, and
    confidence was 85.9 High. It names UK `wholesale_like` (88% of the gross decline), matching the plant.
  - "Did a competitor take our customers?": after a prompt fix, the headline says the data cannot confirm it and
    `data_gaps` lists competitor data. Validated; confidence 59.8 Medium, flagged "no significance test was run".

**Finding for the demo owner:** the expected answer in `data/scenario.yaml` says "driver: fewer active customers; AOV
roughly unchanged". The tools measure an AOV effect of −221,032 (63%) and an active-customers effect of −56,264 (16%).
This is a mix effect: losing big-basket accounts lowers average order value, and new customers kept the headcount
almost flat. The agent is right; the written expectation should be updated.

## Tests executed (2026-10-28, Python 3.12.7 venv)
After session 2: real DB 50 passed / 2 skipped; scenario DB 52 passed; no DB 30 passed / 22 skipped.
Session 1 results below.
The DBs were rebuilt from the tracked `transactions_raw*.parquet` into a temp directory; tracked data was not modified.
| Configuration | Result |
|---|---|
| Real DB (`whybot.duckdb`) | 38 passed, 2 skipped (scenario-only) |
| Scenario DB (`whybot_scenario.duckdb`) | 40 passed |
| No DB | 18 passed, 22 skipped |
| `python src/agent/runner.py` on scenario | E1–E5 ok, invalid request → structured error, confidence 85.9 High |

No linter is configured; `compileall` passes.

## In progress / remaining (by priority)
1. Cache a validated demo run (`loop.py --save`) and add replay for the UI (R17)
2. check_external_factors Tier 1 (R9)
3. Evaluation set (R16)
4. Category classification (R13)
5. STL z-score review (R4)
6. forecast_scenario (optional)

## Blockers
- Gemini capacity and quota limits: 503s and 429s were seen on 2026-10-28. Mitigated by the fallback chain; cache the demo run.
- The STL z-score change needs the data owner's agreement, because it is an analytic behaviour change.

## Decisions and assumptions
- The moved files belong where their docstrings say. They are committed as-is: they are the lead's WIP, and the tests depend on them.
- `docs/MASTER_PROMPT.txt` and `docs/WhyBot_Team_Playbook.pdf` are left untracked; committing them is the owner's call.
- No history rewrite: no blob exceeds 100 MB.

## Git
Base: `origin/main` = `31a58ce` at session start. See `git log` for the commits created in this session and the push result.
