"""pytest tests/test_ground_truth.py -q   tools vs the Excel-derived ground truth (REAL database only)

The ground truth (tests/eval/ground_truth.json) is computed from the raw Excel with pandas, independently of the
DuckDB pipeline, so a mismatch here means a pipeline or tool bug, not an agent problem.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "tools"))
DB = Path(os.environ.get("WHYBOT_DB", ROOT / "data" / "clean" / "whybot.duckdb"))
GT = json.loads((ROOT / "tests" / "eval" / "ground_truth.json").read_text(encoding="utf-8"))
pytestmark = pytest.mark.skipif(not DB.exists() or "scenario" in str(DB), reason="real database not built")
CASES = GT["analytic_cases"]


def req(case):
    f = case["expected_facts"]
    return {"metric": "net_revenue", "period": f["period"], "compare_to": f["compare_to"]}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_detect_anomaly_matches(case):
    from detect_anomaly import detect_anomaly
    r, a = detect_anomaly(req(case)), case["expected_facts"]["anomaly"]
    assert r["change_pct"] == pytest.approx(a["adjusted_change_pct"], abs=0.01)
    assert r["z_score"] == pytest.approx(a["z_score"], abs=0.01)
    assert r["severity"] == a["severity"] and r["direction"] == a["direction"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_drill_down_matches(case):
    from drill_down import drill_down
    f = case["expected_facts"]
    r = drill_down({**req(case), "dimensions": ["country", "uk_vs_international"], "top_k": 3})
    assert r["total_change"] == pytest.approx(f["anomaly"]["net_change"], abs=0.05)
    assert r["contributors"]["country"][0]["value"] == f["country"]["top_country"]
    uk = {x["value"]: x["change"] for x in r["contributors"]["uk_vs_international"]}
    assert uk["United Kingdom"] == pytest.approx(f["country"]["uk_change"], abs=0.05)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_drivers_match(case):
    from driver_decomposition import driver_decomposition
    r, dr = driver_decomposition(req(case)), case["expected_facts"]["drivers"]
    for k, v in dr["effects"].items():
        assert r["effects"][k]["change"] == pytest.approx(v, abs=0.05), k
    assert r["levels"]["current"]["active_customers"] == dr["levels"]["current"]["active_customers"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_movement_matches(case):
    from customer_movement import customer_movement
    r, mv = customer_movement(req(case)), case["expected_facts"]["customer_movement"]
    for g in ("retained", "lost", "new"):
        assert r[g]["customers"] == mv[g]["customers"], g
        assert r[g]["revenue_change"] == pytest.approx(mv[g]["revenue_change"], abs=0.05), g
