# Implementation Roadmap

Derived from `docs/PROJECT_AUDIT.md`, where the R-numbers are defined. Ordering follows the playbook's
"never cut" list: drill_down, the trace view, the computed confidence score and the number validator. Then
dependency order, then the playbook's cut order (Tier-2 external series → forecast_scenario → fancy baselines).

## 1. Critical correctness and integrity
| Item | Expected behaviour | Files | Tests | Done when | Status |
|---|---|---|---|---|---|
| 1.1 Integration modules importable | runner, confidence, validator and significance_test run from their documented paths | `src/agent/*`, `src/tools/significance_test.py` | `tests/test_agent.py`, `tests/test_significance.py` | Smoke run E1–E5 ok; tests pass | **done** |
| 1.2 Repo hygiene | No caches or temp files tracked; secrets ignored | `.gitignore` | `git ls-files` review | Clean index | **done** |
| 1.3 STL z-score sanity (R4) | STL z comparable in scale to yoy z on known dips | `tools/detect_anomaly.py` | Test on 2–3 historic dips | Agreed with Nisaj; robustness no longer inflated | open — needs owner sign-off (analytic change) |

## 2. Foundation and reliability
| Item | Expected behaviour | Files | Tests | Done when | Status |
|---|---|---|---|---|---|
| 2.1 Dependency manifest | `pip install -r requirements.txt` reproduces the env | `requirements.txt` | Full test run | done | **done** |
| 2.2 `.env.example`, model from env | `ANTHROPIC_API_KEY`, `WHYBOT_MODEL` | `.env.example` | — | File present, `.env` ignored | open (with 3.1) |

## 3. Missing core functionality
| Item | Expected behaviour | Files | Tests | Done when | Status |
|---|---|---|---|---|---|
| 3.1 Agent loop (R15) | `run_investigation(question)` generator: Claude tool use over `Runner.tool_schemas()`, at most ~12 steps, stops on `submit_report`. Report validated with `check_report`; one retry on mismatch. Confidence attached by code | `src/agent/loop.py`, `src/agent/prompts/` | Fake-client unit tests; live run on the scenario question | Scenario report names wholesale_like / lost accounts and passes the validator | **blocked for live verification** (no SDK or key); code can start |
| 3.2 check_external_factors Tier 1 (R9) | trading-day delta, price-index change, products dropped/launched, UK holiday flags, `no_data` list | `src/tools/check_external_factors.py` | Contract + reconciliation tests | Validates against contract; ~2 s | open (Mugdha) |
| 3.3 Cache / replay (R17) | Investigation stored, keyed by a hash of the question and tool inputs; replayed offline | `cache/`, `src/agent/loop.py` | Replay test | Demo replays with no network | open |

## 4. Analytics and reporting
| Item | Expected behaviour | Files | Tests | Done when | Status |
|---|---|---|---|---|---|
| 4.1 Category classification (R13) | stock_code → category CSV, cached | `data/external/` or `data/clean/` | Spot-check 50 rows | `category` dimension available | open — method undecided |
| 4.2 Streamlit UI (R18) | Question box, live trace, charts, report card | `src/app/` | Manual | — | open (Mugdha; confirm whether it lives elsewhere) |
| 4.3 forecast_scenario (R10) | Baseline vs what-if | `src/tools/forecast_scenario.py` | Contract test | — | optional (cut order #2) |

## 5. Testing and validation
| Item | Expected behaviour | Files | Tests | Done when | Status |
|---|---|---|---|---|---|
| 5.1 Evaluation set (R16) | 10–15 questions with expected facts, including no-anomaly, unanswerable and tool-error cases | `tests/eval/` | Regression runner | Runs before merges | open (needs 3.1) |
| 5.2 Tool speed | Each tool ~2 s on the full data | — | Timing script | — | open |

## 6. Documentation and developer experience
| Item | Expected behaviour | Files | Tests | Done when | Status |
|---|---|---|---|---|---|
| 6.1 README, CLAUDE.md, audit, status | Accurate setup and run instructions | `README.md`, `CLAUDE.md`, `docs/` | — | — | **done** |

## 7. Hardening (post-hackathon, optional)
SQL in `common.load_lines` uses f-string dates: they are validated by `pd.Timestamp` first, so they are safe today,
but they should become parameters. CI workflow for the DB-free tests. Linting (none configured).
