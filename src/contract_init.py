"""
contract_init.py  -  creates the team's JSON contract and one stub fixture per tool.

Run from the repo root:   pip install jsonschema   then   python src/contract_init.py

Writes   contract/schemas.json          request + response schema for every tool (+ error + report)
         src/tools/stubs/<tool>.json    a valid example response for every tool (fixtures)
Then validates every stub against its schema and exits non-zero on any mismatch.

Contract changes need all three teammates' approval (PR touching contract/schemas.json).
v1 changes versus the playbook's v0 (all driven by the EDA):
  * customer_movement / driver_decomposition carry an `unattributed` term (13.6% of revenue has no customer_id)
  * customer_movement returns window_days (lost = no order in the window; use >= 90 days)
  * detect_anomaly returns `checks` (yoy + stl) for the robustness score and `partial_period_excluded`
  * drill_down rows return n_customers so concentration (EIRE has 3 customers) can be flagged
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contract" / "schemas.json"
STUBS = ROOT / "src" / "tools" / "stubs"

# ---------------------------------------------------------------- schema helpers
def obj(props, required=None):
    return {"type": "object", "properties": props,
            "required": list(props) if required is None else required,
            "additionalProperties": True}

NUM, INT, STR, BOOL = {"type": "number"}, {"type": "integer"}, {"type": "string"}, {"type": "boolean"}
DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
def arr(item): return {"type": "array", "items": item}
def enum(*v): return {"type": "string", "enum": list(v)}

PERIOD = obj({"start": DATE, "end": DATE})
COMPARE = {"oneOf": [enum("previous_period", "same_period_last_year"), PERIOD]}
METRIC = enum("net_revenue", "gross_revenue", "orders", "active_customers", "aov")
FILTERS = arr(obj({"dimension": STR, "value": STR}))
DIMS = arr(enum("country", "uk_vs_international", "customer_type", "tenure", "rfm_tier", "category"))

BASE_REQ = {"metric": METRIC, "period": PERIOD, "compare_to": COMPARE, "filters": FILTERS}
def req(extra=None, required=("metric", "period", "compare_to")):
    props = dict(BASE_REQ); props.update(extra or {})
    return obj(props, list(required))

COMMON_RESP = {"evidence_id": {"type": "string", "pattern": r"^E\d+$"}, "definition": STR, "data_through": DATE}
def resp(props):
    p = dict(COMMON_RESP); p.update(props)
    return obj(p)

SHARE_ROW = obj({"customers": INT, "revenue_change": NUM, "share_of_total_change": NUM})

TOOLS = {
    "detect_anomaly": {
        "request": req(),
        "response": resp({
            "metric": METRIC, "actual": NUM, "expected": NUM, "change_pct": NUM, "z_score": NUM,
            "severity": enum("none", "low", "medium", "high"), "method": enum("yoy", "stl"),
            "baseline": PERIOD,
            "trading_days": obj({"actual": INT, "expected": INT}),
            "trading_day_adjusted": BOOL, "partial_period_excluded": BOOL,
            "checks": arr(obj({"method": enum("yoy", "stl"), "change_pct": NUM, "z_score": NUM})),
        }),
    },
    "drill_down": {
        "request": req({"dimensions": DIMS, "top_k": INT}, ("metric", "period", "compare_to", "dimensions")),
        "response": resp({
            "total_change": NUM, "gross_decline": NUM,
            "contributors": {"type": "object", "additionalProperties": arr(obj({
                "value": STR, "change": NUM, "pct_change": NUM,
                "share_of_net_change": NUM, "share_of_gross_decline": NUM, "n_customers": INT}))},
        }),
    },
    "driver_decomposition": {
        "request": req(),
        "response": resp({
            "total_change": NUM,
            "effects": obj({k: obj({"change": NUM, "share": NUM}) for k in
                            ("active_customers", "orders_per_customer", "aov", "unattributed")}),
            "sum_check": NUM,
        }),
    },
    "customer_movement": {
        "request": req({"window_days": INT}),
        "response": resp({
            "window_days": INT, "total_change": NUM,
            "retained": SHARE_ROW, "lost": SHARE_ROW, "new": SHARE_ROW,
            "unattributed": obj({"revenue_change": NUM, "share_of_total_change": NUM}),
            "top10_concentration": NUM, "concentrated_in_few_accounts": BOOL,
            "top_lost_accounts": arr(obj({"customer_id": INT, "revenue_change": NUM})),
        }),
    },
    "significance_test": {
        "request": req({"segment": obj({"dimension": STR, "value": STR})}, ("metric", "period", "compare_to", "segment")),
        "response": resp({
            "method": STR, "effect_size": NUM, "ci_low": NUM, "ci_high": NUM, "p_value": NUM,
            "strength": enum("supporting", "weak", "none"), "n_customers": INT, "caveat": STR,
        }),
    },
    "check_external_factors": {
        "request": req(),
        "response": resp({
            "trading_days": obj({"actual": INT, "expected": INT, "delta": INT}),
            "price_index_change_pct": NUM, "products_dropped": INT, "products_launched": INT,
            "holiday_flags": arr(STR),
            "tested_series": arr(obj({"name": STR, "effect": NUM, "strength": enum("supporting", "weak", "none")})),
            "no_data": arr(STR),
        }),
    },
    "forecast_scenario": {
        "request": req({"segment": obj({"dimension": STR, "value": STR}), "horizon_weeks": INT},
                       ("metric", "period", "compare_to")),
        "response": resp({
            "horizon_weeks": INT, "method": STR, "caveat": STR,
            "baseline": arr(obj({"week_start": DATE, "value": NUM})),
            "what_if": arr(obj({"week_start": DATE, "value": NUM})),
        }),
    },
    "compute_confidence": {
        "request": obj({"evidence_ids": arr(STR)}),
        "response": obj({
            "score": NUM, "band": enum("High", "Medium", "Low"),
            "components": obj({"coverage": NUM, "significance": NUM, "specificity": NUM, "robustness": NUM}),
            "flags": arr(STR),
        }),
    },
}

ERROR = obj({"error": obj({"code": enum("EMPTY_SLICE", "BAD_PERIOD", "TINY_BASE", "NO_DATA", "INTERNAL"),
                           "message": STR})})

REPORT = obj({
    "headline": STR,
    "findings": arr(obj({"text": STR, "evidence_ids": arr(STR)})),
    "actions": arr(obj({"text": STR, "evidence_ids": arr(STR)}, ["text"])),
    "hypotheses": arr(STR),
    "data_gaps": arr(STR),
}, ["headline", "findings", "actions", "hypotheses"])

# ---------------------------------------------------------------- stub fixtures (valid examples)
P = {"start": "2011-09-26", "end": "2011-11-20"}
D = "net_revenue = sum(quantity*unit_price) over product lines; cancellations negative; unattributed rows included"
T = "2011-12-09"
def stub(**kw): return {"evidence_id": "E1", "definition": D, "data_through": T, **kw}

STUBS_DATA = {
    "detect_anomaly": stub(
        metric="net_revenue", actual=812340.55, expected=941200.10, change_pct=-13.7, z_score=-2.4,
        severity="high", method="yoy", baseline={"start": "2010-09-27", "end": "2010-11-21"},
        trading_days={"actual": 38, "expected": 40}, trading_day_adjusted=True, partial_period_excluded=True,
        checks=[{"method": "yoy", "change_pct": -13.7, "z_score": -2.4},
                {"method": "stl", "change_pct": -11.9, "z_score": -2.1}]),
    "drill_down": stub(
        evidence_id="E2", total_change=-128860.2, gross_decline=-146500.0,
        contributors={
            "country": [
                {"value": "United Kingdom", "change": -91200.0, "pct_change": -11.1, "share_of_net_change": 0.71,
                 "share_of_gross_decline": 0.62, "n_customers": 1480},
                {"value": "Germany", "change": -12400.0, "pct_change": -18.0, "share_of_net_change": 0.10,
                 "share_of_gross_decline": 0.08, "n_customers": 52}],
            "customer_type": [
                {"value": "wholesale_like", "change": -101000.0, "pct_change": -15.2, "share_of_net_change": 0.78,
                 "share_of_gross_decline": 0.69, "n_customers": 640}]}),
    "driver_decomposition": stub(
        evidence_id="E3", total_change=-128860.2, sum_check=-128860.2,
        effects={"active_customers": {"change": -88500.0, "share": 0.69},
                 "orders_per_customer": {"change": -21000.0, "share": 0.16},
                 "aov": {"change": -9360.2, "share": 0.07},
                 "unattributed": {"change": -10000.0, "share": 0.08}}),
    "customer_movement": stub(
        evidence_id="E4", window_days=90, total_change=-128860.2,
        retained={"customers": 910, "revenue_change": -20000.0, "share_of_total_change": 0.16},
        lost={"customers": 210, "revenue_change": -98000.0, "share_of_total_change": 0.76},
        new={"customers": 150, "revenue_change": 14000.0, "share_of_total_change": -0.11},
        unattributed={"revenue_change": -24860.2, "share_of_total_change": 0.19},
        top10_concentration=0.41, concentrated_in_few_accounts=False,
        top_lost_accounts=[{"customer_id": 14646, "revenue_change": -9800.0},
                           {"customer_id": 18102, "revenue_change": -7400.0}]),
    "significance_test": stub(
        evidence_id="E5", method="customer-level bootstrap, 2000 resamples", effect_size=-0.152,
        ci_low=-0.231, ci_high=-0.074, p_value=0.003, strength="supporting", n_customers=640,
        caveat="Segment chosen after viewing the data; p-values are optimistic. Prefer the interval."),
    "check_external_factors": stub(
        evidence_id="E6", trading_days={"actual": 38, "expected": 40, "delta": -2},
        price_index_change_pct=1.8, products_dropped=34, products_launched=21,
        holiday_flags=["UK summer bank holiday not in window"],
        tested_series=[{"name": "GBP/EUR weekly change vs non-UK revenue", "effect": 0.04, "strength": "none"}],
        no_data=["competitor pricing", "marketing spend"]),
    "forecast_scenario": stub(
        evidence_id="E7", horizon_weeks=4, method="exponential smoothing with yoy seasonal index",
        caveat="Simple projection; not a causal estimate.",
        baseline=[{"week_start": "2011-11-21", "value": 210000.0}, {"week_start": "2011-11-28", "value": 205000.0}],
        what_if=[{"week_start": "2011-11-21", "value": 232000.0}, {"week_start": "2011-11-28", "value": 227000.0}]),
    "compute_confidence": {
        "score": 74.5, "band": "Medium",
        "components": {"coverage": 0.78, "significance": 0.85, "specificity": 0.71, "robustness": 0.5},
        "flags": []},
    "error_example": {"error": {"code": "EMPTY_SLICE", "message": "No product lines match the filter."}},
    "report_example": {
        "headline": "Net revenue fell 13.7% year over year, driven by lost wholesale-like customers.",
        "findings": [{"text": "Wholesale-like customers explain 78% of the net decline.", "evidence_ids": ["E2"]}],
        "actions": [{"text": "Contact the top lost accounts.", "evidence_ids": ["E4"]}],
        "hypotheses": ["A competitor may have won these accounts (cannot be verified from this data)."]},
}

# ---------------------------------------------------------------- main
def main():
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    STUBS.mkdir(parents=True, exist_ok=True)

    contract = {"version": "1.0", "tools": TOOLS, "error": ERROR, "report": REPORT}
    CONTRACT.write_text(json.dumps(contract, indent=2), encoding="utf-8")

    failures = 0
    for name, data in STUBS_DATA.items():
        (STUBS / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        if name in TOOLS: schema = TOOLS[name]["response"]
        elif name == "error_example": schema = ERROR
        else: schema = REPORT
        errs = sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: list(e.path))
        print(("PASS  " if not errs else "FAIL  ") + name)
        for e in errs:
            failures += 1
            print("      ", "/".join(map(str, e.path)) or "(root)", "->", e.message)

    # sample request validation
    sample_req = {"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year"}
    for name, t in TOOLS.items():
        if name in ("detect_anomaly", "check_external_factors", "driver_decomposition", "customer_movement"):
            errs = list(Draft7Validator(t["request"]).iter_errors(sample_req))
            print(("PASS  " if not errs else "FAIL  ") + f"{name} request")
            failures += len(errs)

    print(f"\nWrote {CONTRACT.relative_to(ROOT)} and {len(STUBS_DATA)} stubs in {STUBS.relative_to(ROOT)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()