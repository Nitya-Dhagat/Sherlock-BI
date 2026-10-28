"""pytest tests/test_tools.py -q     (set WHYBOT_DB first to test the scenario database)"""
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


def validate(tool, payload):
    payload = {"evidence_id": "E1", **payload}
    errs = [e.message for e in Draft7Validator(CONTRACT["tools"][tool]["response"]).iter_errors(payload)]
    assert not errs, errs


@pytest.mark.parametrize("metric", ["net_revenue", "gross_revenue", "orders"])
def test_drill_down_slices_sum_to_total(metric):
    from drill_down import drill_down
    r = drill_down({"metric": metric, "period": P, "compare_to": "same_period_last_year",
                    "dimensions": ["country", "uk_vs_international", "customer_type", "tenure", "rfm_tier"], "top_k": 3})
    validate("drill_down", r)
    for dim, rows in r["contributors"].items():
        assert abs(sum(x["change"] for x in rows) - r["total_change"]) < 0.05 * len(rows), dim


def test_drill_down_gross_decline_shares_sum_to_one():
    from drill_down import drill_down
    r = drill_down({"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year",
                    "dimensions": ["country"], "top_k": 50})
    shares = [x["share_of_gross_decline"] for x in r["contributors"]["country"]]
    assert abs(sum(shares) - 1.0) < 0.01


def test_drill_down_category_is_skipped_not_crashing():
    from drill_down import drill_down
    r = drill_down({"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year",
                    "dimensions": ["country", "category"]})
    assert r["skipped_dimensions"] == ["category"]


def test_detect_anomaly_schema_and_fields():
    from detect_anomaly import detect_anomaly
    r = detect_anomaly({"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year"})
    validate("detect_anomaly", r)
    assert r["method"] == "yoy" and r["trading_day_adjusted"] is True


def test_detect_anomaly_clamps_partial_final_week():
    from detect_anomaly import detect_anomaly
    r = detect_anomaly({"metric": "net_revenue", "period": {"start": "2011-10-31", "end": "2011-12-09"},
                        "compare_to": "same_period_last_year"})
    assert r["partial_period_excluded"] is True and r["period_used"]["end"] == "2011-12-04"


def test_detect_anomaly_bad_period_returns_structured_error():
    from common import ToolError
    from detect_anomaly import detect_anomaly
    with pytest.raises(ToolError) as ei:
        detect_anomaly({"metric": "net_revenue", "period": {"start": "2009-12-10", "end": "2010-01-20"},
                        "compare_to": "same_period_last_year"})
    assert ei.value.code == "BAD_PERIOD"


def test_detect_anomaly_unsupported_metric():
    from common import ToolError
    from detect_anomaly import detect_anomaly
    with pytest.raises(ToolError):
        detect_anomaly({"metric": "active_customers", "period": P, "compare_to": "same_period_last_year"})