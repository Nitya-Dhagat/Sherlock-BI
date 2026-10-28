"""pytest tests/test_significance.py -q   (set WHYBOT_DB to data/scenario/whybot_scenario.duckdb for the planted check)"""
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
WHOLESALE = {"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year",
             "segment": {"dimension": "customer_type", "value": "wholesale_like"}}


def test_significance_schema_and_interval():
    from significance_test import significance_test
    r = significance_test(WHOLESALE)
    errs = [e.message for e in Draft7Validator(CONTRACT["tools"]["significance_test"]["response"]).iter_errors(
        {"evidence_id": "E1", **r})]
    assert not errs, errs
    assert r["ci_low"] <= r["ci_high"] and 0 < r["p_value"] <= 1 and r["n_customers"] >= 10


def test_significance_is_reproducible():
    from significance_test import significance_test
    assert significance_test(WHOLESALE) == significance_test(WHOLESALE)


@pytest.mark.parametrize("segment, code", [({"dimension": "customer_type", "value": "unattributed"}, "NO_DATA"),
                                           ({"dimension": "category", "value": "x"}, "NO_DATA"),
                                           ({"dimension": "country", "value": "Atlantis"}, "EMPTY_SLICE")])
def test_significance_structured_errors(segment, code):
    from common import ToolError
    from significance_test import significance_test
    with pytest.raises(ToolError) as ei:
        significance_test({**WHOLESALE, "segment": segment})
    assert ei.value.code == code


@pytest.mark.skipif(not SCEN, reason="scenario database only")
def test_scenario_segment_is_supporting():
    from significance_test import significance_test
    r = significance_test(WHOLESALE)
    assert r["effect_size"] < 0 and r["ci_high"] < 0 and r["strength"] == "supporting"
