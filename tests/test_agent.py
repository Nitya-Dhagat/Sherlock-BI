"""pytest tests/test_agent.py -q     confidence score, number validator and runner guards (no database needed)"""
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "agent"))
sys.path.insert(0, str(ROOT / "src" / "tools"))
CONTRACT = json.loads((ROOT / "contract" / "schemas.json").read_text())

from confidence import compute_confidence  # noqa: E402
from validator import check_report  # noqa: E402


def ev(eid, tool, result):
    return {"id": eid, "tool": tool, "input": {}, "result": {"evidence_id": eid, **result}}


ANOMALY = ev("E1", "detect_anomaly", {"change_pct": -15.0, "z_score": -2.4, "actual": 1908898.12,
                                      "checks": [{"method": "yoy", "change_pct": -15.0, "z_score": -2.4},
                                                 {"method": "stl", "change_pct": -12.0, "z_score": -1.8}]})
DRILL = ev("E2", "drill_down", {"total_change": -298860.0, "contributors": {
    "customer_type": [{"value": "wholesale_like", "share_of_gross_decline": 0.8},
                      {"value": "retail_like", "share_of_gross_decline": 0.2}],
    "tenure": [{"value": "returning", "share_of_gross_decline": 1.0}]}})
SIG = ev("E3", "significance_test", {"strength": "supporting", "effect_size": -0.742})


# ---------------------------------------------------------------- compute_confidence
def test_confidence_formula_by_hand():
    r = compute_confidence([ANOMALY, DRILL, SIG])
    # C = 0.8 (tenure skipped), S = 0.5*1 + 0.5*2.4/3 = 0.9, P = 0.8^2 + 0.2^2 = 0.68, R = 2/2
    assert r["components"] == {"coverage": 0.8, "significance": 0.9, "specificity": 0.68, "robustness": 1.0}
    assert r["score"] == pytest.approx(83.6) and r["band"] == "High" and r["flags"] == []


def test_confidence_validates_against_contract():
    r = compute_confidence([ANOMALY, DRILL, SIG])
    assert not list(Draft7Validator(CONTRACT["tools"]["compute_confidence"]["response"]).iter_errors(r))


def test_confidence_empty_evidence_is_low_and_flagged():
    r = compute_confidence([])
    assert r["score"] == 0 and r["band"] == "Low" and "no significance test was run" in r["flags"]


def test_confidence_single_check_caps_robustness():
    one = ev("E1", "detect_anomaly", {"change_pct": -15.0, "z_score": -2.4,
                                      "checks": [{"method": "yoy", "change_pct": -15.0, "z_score": -2.4}]})
    assert compute_confidence([one])["components"]["robustness"] == 0.5


def test_confidence_disagreeing_check_not_counted():
    bad = ev("E1", "detect_anomaly", {"change_pct": -15.0, "z_score": -2.4,
                                      "checks": [{"method": "yoy", "change_pct": -15.0, "z_score": -2.4},
                                                 {"method": "stl", "change_pct": 3.0, "z_score": 0.4}]})
    assert compute_confidence([bad])["components"]["robustness"] == 0.5


def test_confidence_concentration_flag():
    mv = ev("E4", "customer_movement", {"concentrated_in_few_accounts": True})
    assert "concentrated in few accounts" in compute_confidence([ANOMALY, DRILL, SIG, mv])["flags"]


def test_confidence_weak_significance_counts_half():
    weak = ev("E3", "significance_test", {"strength": "weak"})
    assert compute_confidence([ANOMALY, DRILL, weak])["components"]["significance"] == pytest.approx(0.65)


# ---------------------------------------------------------------- validator
EVIDENCE = [ANOMALY, DRILL, SIG]


def report(headline, findings=None, **kw):
    return {"headline": headline, "findings": findings if findings is not None else [], "actions": [],
            "hypotheses": [], **kw}


def test_validator_accepts_rounded_tool_numbers():
    r = check_report(report("Net revenue fell 15.0% to $1.9M between 2011-09-26 and 2011-11-20 (E1)",
                            [{"text": "Wholesale-like accounts carry 80% of the decline; effect 74.2%; z-score 2.4",
                              "evidence_ids": ["E2", "E3"]}]), EVIDENCE)
    assert r["ok"], r


def test_validator_rejects_invented_number():
    r = check_report(report("Net revenue fell by $2.5M"), EVIDENCE)
    assert not r["ok"] and "$2.5M" in r["unmatched"]


def test_validator_ignores_dates_years_ids_and_small_counts():
    r = check_report(report("In 2011 the top 3 accounts (E2) changed on 2011-10-03"), EVIDENCE)
    assert r["ok"] and r["checked"] == 0


def test_validator_requires_citations():
    r = check_report(report("x", [{"text": "Revenue fell", "evidence_ids": []}]), EVIDENCE)
    assert not r["ok"] and r["bad_citations"]


def test_validator_rejects_unknown_evidence_id():
    r = check_report(report("x", [{"text": "Revenue fell", "evidence_ids": ["E9"]}],
                            actions=[{"text": "Call them", "evidence_ids": ["E8"]}]), EVIDENCE)
    assert set(r["bad_citations"]) == {"unknown evidence id E9", "unknown evidence id E8"}


def test_validator_checks_hypotheses_too():
    r = check_report(report("x", hypotheses=["A competitor cut prices by 37%"]), EVIDENCE)
    assert not r["ok"] and "37%" in r["unmatched"]


def test_report_example_stub_matches_report_schema():
    stub = json.loads((ROOT / "src" / "tools" / "stubs" / "report_example.json").read_text())
    assert not list(Draft7Validator(CONTRACT["report"]).iter_errors(stub))


# ---------------------------------------------------------------- runner guards (no database touched)
@pytest.fixture
def runner_mod():
    import runner
    return runner


def test_runner_offers_contract_schemas(runner_mod):
    for s in runner_mod.Runner().tool_schemas():
        assert s["name"] in CONTRACT["tools"] and s["description"] and s["input_schema"]["type"] == "object"


def test_runner_unknown_tool_is_structured_error(runner_mod):
    r = runner_mod.Runner()
    payload, eid = r.call("no_such_tool", {})
    assert eid is None and payload["error"]["code"] == "NO_DATA" and r.evidence == []


def test_runner_converts_exceptions_and_missing_args(runner_mod, monkeypatch):
    def boom(_):
        raise ValueError("kaboom")

    def needs_metric(args):
        return args["metric"]
    monkeypatch.setitem(runner_mod.TOOL_FUNCS, "drill_down", boom)
    monkeypatch.setitem(runner_mod.TOOL_FUNCS, "detect_anomaly", needs_metric)
    r = runner_mod.Runner()
    assert r.call("drill_down", {})[0]["error"]["code"] == "INTERNAL"
    assert r.call("detect_anomaly", {})[0]["error"]["code"] == "BAD_PERIOD"
    assert r.evidence == []


def test_runner_rejects_contract_violation_and_numbers_valid_results(runner_mod, monkeypatch):
    stub = json.loads((ROOT / "src" / "tools" / "stubs" / "detect_anomaly.json").read_text())
    stub.pop("evidence_id", None)
    monkeypatch.setitem(runner_mod.TOOL_FUNCS, "detect_anomaly", lambda _: dict(stub))
    monkeypatch.setitem(runner_mod.TOOL_FUNCS, "drill_down", lambda _: {"total_change": "not a number"})
    r = runner_mod.Runner()
    bad, bad_id = r.call("drill_down", {})
    assert bad_id is None and "violates the contract" in bad["error"]["message"]
    ok1, id1 = r.call("detect_anomaly", {})
    ok2, id2 = r.call("detect_anomaly", {})
    assert (id1, id2) == ("E1", "E2") and ok2["evidence_id"] == "E2" and len(r.evidence) == 2
