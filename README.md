# Sherlock-BI (WhyBot)

An AI business analyst for the UCI Online Retail II dataset. Ask *"Why did revenue drop?"*: deterministic tools
confirm the drop, find where it happened and what changed in customer behaviour, and test whether the finding is
solid. An LLM chooses the tools and writes the report, but it may quote only numbers that the tools returned.

## Quick start (Python 3.12)
```
python -m venv venv
venv\Scripts\activate                      # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python src/build_data.py                   # data/raw/online_retail_II.xlsx -> data/clean/transactions_raw.parquet
python src/build_clean.py                  # -> data/clean/whybot.duckdb (+ metric tables, clean_report.json)
python src/make_scenario.py                # optional: labelled synthetic demo DB in data/scenario/
```
Point the tools at a database with `WHYBOT_DB` (default `data/clean/whybot.duckdb`):
```
$env:WHYBOT_DB = "data/scenario/whybot_scenario.duckdb"   # PowerShell
python src/agent/runner.py                                 # runs every tool + confidence score
python -m pytest tests -q
```
DuckDB files are generated and are not committed.

## Metric definitions
See `src/tools/common.py` (`DEFINITIONS`) and `docs/WhyBot_Team_Playbook.pdf` §3.3. net_revenue counts
cancellations as negatives; gross_revenue excludes them; rows without a customer ID are reported as "unattributed".

## Docs
- `CLAUDE.md` — developer guide
- `docs/PROJECT_AUDIT.md` — requirements matrix and audit
- `docs/IMPLEMENTATION_ROADMAP.md`
- `docs/IMPLEMENTATION_STATUS.md`

## Owners
Nitya — agent, validator, confidence, integration · Nisaj — data layer and core tools · Mugdha — statistics,
external factors, forecast, UI, demo scenario.
