"""pytest tests/test_tools_2.py -q   (set WHYBOT_DB to data/scenario/whybot_scenario.duckdb for the planted checks)"""
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "tools"))
DB = Path(os.environ.get("WHYBOT_DB", ROOT / "data" / "clean" / "whybot.duckdb"))
pytestmark = pytest.mark.skipif(not DB.exists(), reason="database not built")
CONTRACT = json.loads((ROOT / "contract" / "schemas.json").read_text())
P = {"start": "2011-09-26", "end": "2011-11-20"}
SCEN = "scenario" in str(DB)


def validate(tool, payload):
    errs = [e.message for e in Draft7Validator(CONTRACT["tools"][tool]["response"]).iter_errors(
        {"evidence_id": "E1", **payload})]
    assert not errs, errs


@pytest.mark.parametrize("metric", ["net_revenue", "gross_revenue"])
def test_driver_effects_sum_to_total(metric):
    from driver_decomposition import driver_decomposition
    r = driver_decomposition({"metric": metric, "period": P, "compare_to": "same_period_last_year"})
    validate("driver_decomposition", r)
    assert abs(sum(v["change"] for v in r["effects"].values()) - r["total_change"]) < 0.05
    assert abs(r["sum_check"] - r["total_change"]) < 0.05


@pytest.mark.parametrize("metric", ["net_revenue", "gross_revenue"])
def test_movement_groups_sum_to_total(metric):
    from customer_movement import customer_movement
    r = customer_movement({"metric": metric, "period": P, "compare_to": "same_period_last_year"})
    validate("customer_movement", r)
    s = r["retained"]["revenue_change"] + r["lost"]["revenue_change"] + r["new"]["revenue_change"] \
        + r["unattributed"]["revenue_change"]
    assert abs(s - r["total_change"]) < 0.05


def test_movement_with_filter_still_reconciles():
    from customer_movement import customer_movement
    r = customer_movement({"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year",
                           "filters": [{"dimension": "customer_type", "value": "wholesale_like"}]})
    s = sum(r[k]["revenue_change"] for k in ("retained", "lost", "new", "unattributed"))
    assert abs(s - r["total_change"]) < 0.05


def test_short_window_warns():
    from customer_movement import customer_movement
    r = customer_movement({"metric": "net_revenue", "period": {"start": "2011-10-03", "end": "2011-10-09"},
                           "compare_to": "same_period_last_year", "window_days": 7})
    assert "warning" in r


@pytest.mark.skipif(not SCEN, reason="scenario database only")
def test_scenario_is_recovered():
    from customer_movement import customer_movement
    from driver_decomposition import driver_decomposition
    d = driver_decomposition({"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year"})
    assert d["total_change"] < -250000
    assert d["effects"]["active_customers"]["change"] < 0
    m = customer_movement({"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year"})
    assert m["lost"]["customers"] >= 30 and m["lost"]["revenue_change"] < -250000