# Implementation Status

_Last updated: 2026-10-28_

**Stage:** partial implementation. The analytic layer and integration layer work and are tested. The agent loop and
UI are missing. Not production-ready. Details: `PROJECT_AUDIT.md`. Plan: `IMPLEMENTATION_ROADMAP.md`.

## Completed (this session)
- Relocated the untracked WIP modules to their documented homes, contents unchanged. They failed to import before the move.
  `src/agent/{runner,confidence,validator}.py`, `src/tools/significance_test.py`
- Added `tests/test_agent.py`: confidence formula by hand, contract validity, edge cases; validator rounding,
  units, citations and hypotheses; runner error guards and evidence numbering. None of these need a database.
- Added `tests/test_significance.py`: schema, reproducibility, structured errors, and recovery of the scenario plant.
- Repo hygiene: fixed `.gitignore`; untracked `__pycache__`, `.DS_Store` and `tempCodeRunnerFile.py` (local files kept);
  added `requirements.txt` (adds `statsmodels`, which was missing).
- Docs: `README.md`, `CLAUDE.md`, `docs/PROJECT_AUDIT.md`, `docs/IMPLEMENTATION_ROADMAP.md`, this file.

## Tests executed (2026-10-28, Python 3.12.7 venv)
The DBs were rebuilt from the tracked `transactions_raw*.parquet` into a temp directory; tracked data was not modified.
| Configuration | Result |
|---|---|
| Real DB (`whybot.duckdb`) | 38 passed, 2 skipped (scenario-only) |
| Scenario DB (`whybot_scenario.duckdb`) | 40 passed |
| No DB | 18 passed, 22 skipped |
| `python src/agent/runner.py` on scenario | E1–E5 ok, invalid request → structured error, confidence 85.9 High |

No linter is configured; `compileall` passes.

## In progress / remaining (by priority)
1. Agent loop `run_investigation()` + `submit_report` + validator retry (R15). **Blocked for live verification.**
2. check_external_factors Tier 1 (R9)
3. Cache / replay (R17)
4. Evaluation set (R16)
5. Category classification (R13)
6. UI (R18)
7. STL z-score review (R4)
8. forecast_scenario (optional)

## Blockers
- `anthropic` SDK is not installed, and there is no API key / `.env` on this machine, so the Claude loop can't run live.
- The Streamlit UI isn't in this repo; whether it exists elsewhere is unknown.
- The STL z-score change needs the data owner's agreement, because it is an analytic behaviour change.

## Decisions and assumptions
- The moved files belong where their docstrings say. They are committed as-is: they are the lead's WIP, and the tests depend on them.
- `docs/MASTER_PROMPT.txt` and `docs/WhyBot_Team_Playbook.pdf` are left untracked; committing them is the owner's call.
- No history rewrite: no blob exceeds 100 MB.

## Git
Base: `origin/main` = `31a58ce` at session start. See `git log` for the commits created in this session and the push result.
