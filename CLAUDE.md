# CLAUDE.md — Sherlock-BI (WhyBot)

WhyBot answers "Why did revenue drop?" on UCI Online Retail II. An LLM picks deterministic tools and writes the report;
**every number comes from code**, the validator rejects any number not returned by a tool, and the confidence score
is computed by code. Requirements: `docs/WhyBot_Team_Playbook.pdf`. Status: `docs/IMPLEMENTATION_STATUS.md`.
Audit and requirements matrix: `docs/PROJECT_AUDIT.md`. Roadmap: `docs/IMPLEMENTATION_ROADMAP.md`.

## Layout
- `src/build_data.py` — raw Excel (both sheets) → `data/clean/transactions_raw.parquet`
- `src/build_clean.py` — dedupe, flags, product lines, metric tables → `data/clean/whybot.duckdb` + parquet/csv/report. `main(raw_parquet, out_dir, db_name)` writes only under `out_dir`. Also provides `customer_features_asof` (no look-ahead).
- `src/make_scenario.py` — labelled **synthetic** plant (30 UK wholesale-like accounts stop after 2011-09-26) → `data/scenario/`, `data/scenario.yaml` (expected answer)
- `src/eda.py` — EDA report (`data/eda/`)
- `src/contract_init.py` — generates `contract/schemas.json` and the `src/tools/stubs/*.json` fixtures. Contract changes need all three owners' approval
- `src/tools/common.py` — period resolution (clamps the partial final week; yoy = −364 days), segments fixed as of period start, `DEFINITIONS`, `ToolError(code, message)`
- `src/tools/` — `detect_anomaly`, `drill_down`, `driver_decomposition`, `customer_movement`, `significance_test`. Each takes a request dict and returns a dict, or raises `ToolError`
- `src/agent/runner.py` — `Runner.call(name, args)`: evidence ids E1…, contract validation, never raises; `tool_schemas()` for Claude
- `src/agent/confidence.py` — `compute_confidence(evidence)` (0.35C + 0.30S + 0.20P + 0.15R; bands ≥75 High, 50–74 Medium)
- `src/agent/validator.py` — `check_report(report, evidence)` (numbers must match evidence; findings must cite valid ids)
- **Not yet built:** agent loop (`loop.py` / `run_investigation`), `check_external_factors`, `forecast_scenario`, category classification, Streamlit UI, cache/replay, evaluation set.

## Setup and run (Windows, Python 3.12.7 venv)
```
python -m venv venv ; venv\Scripts\activate ; pip install -r requirements.txt
python src/build_data.py ; python src/build_clean.py      # ~1 min; DB files are NOT in Git
python src/make_scenario.py                                 # demo DB
$env:WHYBOT_DB = "data/scenario/whybot_scenario.duckdb"     # tools read this (default data/clean/whybot.duckdb)
python src/agent/runner.py                                  # smoke test: all tools + confidence
python -m pytest tests -q                                   # DB tests skip if WHYBOT_DB is missing
```
Run the tests against both DBs. The scenario-only tests check that the plant is recovered.

## Business rules (do not change silently)
- Product line = stock code `^[0-9]{5}[A-Z]{0,2}$`, unit_price > 0, not a bulk buy-and-cancel pair (≥10,000 units)
- net_revenue includes cancellations as negatives; gross excludes them. orders = distinct non-C invoices; aov = gross/orders
- Rows without a customer_id count in revenue and are reported as `unattributed` (~13.6% of revenue)
- Never flag the incomplete final week (data ends 2011-12-09; the last complete week ends 2011-12-04)
- Slices, effects and movement groups must sum exactly to the total; the tools assert this and return `INTERNAL` otherwise
- Significance strength labels are fixed constants in code; the LLM never chooses them
- Report language: "driven by" / "consistent with"; unverifiable ideas go under hypotheses and are not scored

## Data and Git rules
- Never commit `*.duckdb`, `*.duckdb.wal`, `.env`, caches or temp files. Do not delete local data files.
- Raw Excel (45.6 MB) and some Parquet files are tracked; every blob is under GitHub's 100 MB limit.
- To test without touching tracked data, build into a temp dir: `build_clean.main(raw_parquet=..., out_dir=<tmp>)`.
- Playbook rule: small PRs and contract changes via PR. Never force-push or rewrite history.

## Conventions
Compact tool outputs (top-k, rounded); every result carries `definition` and `data_through`. Errors use
`{"error": {"code": EMPTY_SLICE|BAD_PERIOD|TINY_BASE|NO_DATA|INTERNAL, "message"}}`. Module docstrings document the
method and the run command. Tests live in `tests/` and use `jsonschema` against the contract.

## Known limitations / open questions
- No Anthropic SDK or API key is configured on this machine, so the agent loop can't be verified live
- The STL z-score in detect_anomaly looks overconfident (−13.75 vs yoy −1.69 on the demo window) and inflates robustness
- The validator flags prose dates and week numbers, so the prompt must require ISO dates
- Whether the Streamlit UI exists outside this repo is unknown
