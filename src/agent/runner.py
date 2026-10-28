"""
runner.py - one door for every tool call.
  * discovers the tools that exist (a tool whose module is missing or broken is simply not offered to the agent)
  * adds evidence ids E1, E2, ... and keeps the evidence store
  * validates every result against contract/schemas.json (an invalid result is returned as a structured error)
  * turns exceptions into {"error": {"code", "message"}}
  * builds the Claude tool schemas from the contract
Smoke test:  python src/agent/runner.py     (set WHYBOT_DB first)
"""
import copy
import importlib
import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "tools"))
from common import ToolError  # noqa: E402

CONTRACT = json.loads((ROOT / "contract" / "schemas.json").read_text(encoding="utf-8"))
ERROR_CODES = set(CONTRACT["error"]["properties"]["error"]["properties"]["code"]["enum"])
MODULES = ["detect_anomaly", "drill_down", "driver_decomposition", "customer_movement",
           "significance_test", "check_external_factors", "forecast_scenario"]
TOOL_FUNCS = {}
for _n in MODULES:
    try:
        TOOL_FUNCS[_n] = getattr(importlib.import_module(_n), _n)
    except Exception as _e:  # noqa: BLE001  (a missing tool must never break the demo)
        print(f"[runner] tool '{_n}' not available: {type(_e).__name__}: {_e}", file=sys.stderr)

def _resolved(args):
    """Shared connection + the same period resolution every core tool uses (partial week clamped, yoy = -364 d)."""
    from common import d, get_con, prepare_period
    con = get_con()
    p = prepare_period(con, args["period"], args.get("compare_to", "same_period_last_year"))
    return con, {"start": d(p["s"]), "end": d(p["e"])}, {"start": d(p["bs"]), "end": d(p["be"])}, d(p["dt"])


def _external(args, _raw=TOOL_FUNCS.get("check_external_factors")):
    """Contract request -> Mugdha's tool (explicit ranges + DB connection) -> contract fields added alongside hers."""
    con, cur, base, dt = _resolved(args)
    r = _raw({"period": cur, "compare_to": base, "filters": args.get("filters") or []}, con=con)
    if "error" in r:
        return r
    t1 = r.get("tier1") or {}
    td, av, pr = t1.get("trading_days") or {}, t1.get("availability") or {}, t1.get("price") or {}
    tested = r.get("tier2_tested") or []
    r.update(trading_days={"actual": td.get("current"), "expected": td.get("base"), "delta": td.get("delta")},
             price_index_change_pct=pr.get("price_index_change_pct"),
             products_dropped=av.get("products_dropped"), products_launched=av.get("products_launched"),
             holiday_flags=[str(x.get("name") or x.get("series")) for x in tested
                            if "holiday" in str(x.get("name") or x.get("series", "")).lower()],
             tested_series=[{"name": str(x.get("name") or x.get("series")), "effect": x.get("effect", 0.0),
                             "strength": x.get("strength", "none")} for x in tested],
             no_data=[f"{x['series']}: {x['reason']}" if isinstance(x, dict) else str(x)
                      for x in r.get("no_data") or []],
             data_through=dt)
    return r


def _forecast(args, _raw=TOOL_FUNCS.get("forecast_scenario")):
    con, cur, _, dt = _resolved(args)
    seg = args.get("segment")
    r = _raw({"horizon_weeks": args.get("horizon_weeks", 8), "filters": args.get("filters") or [],
              "recover_segment": [seg] if seg else [], "as_of": cur["start"], "data_through": dt}, con=con)
    if "error" in r:
        return r
    pts = lambda xs: [{"week_start": p["period_start"], "value": p["value"]} for p in xs or []]  # noqa: E731
    r.update(baseline=pts(r.get("baseline")), what_if=pts(r.get("what_if")), data_through=dt)
    return r


if "check_external_factors" in TOOL_FUNCS:
    TOOL_FUNCS["check_external_factors"] = _external
if "forecast_scenario" in TOOL_FUNCS:
    TOOL_FUNCS["forecast_scenario"] = _forecast

DESCRIPTIONS = {
    "detect_anomaly": "Check whether a metric really changed in a period versus the same period last year "
                      "(trading-day adjusted, with a z-score from history and an STL cross-check). Always call first.",
    "drill_down": "Split the change in net_revenue/gross_revenue/orders across dimension values "
                  "(country, uk_vs_international, customer_type, tenure, rfm_tier). Slices sum exactly to the total.",
    "driver_decomposition": "Split the change into active customers, orders per customer, revenue per order (AOV) "
                            "and unattributed revenue. Effects sum exactly to the total change.",
    "customer_movement": "Split the change into retained, lost and new customers plus unattributed revenue; "
                         "returns top lost accounts and a concentration flag.",
    "significance_test": "Customer-level bootstrap for one segment: effect size, 95% interval, strength label.",
    "check_external_factors": "Check non-customer explanations: trading days, price changes, product availability, "
                              "holidays; lists what cannot be checked.",
    "forecast_scenario": "Project the next weeks with and without a segment (what-if). Simple method, with a caveat.",
}


class Runner:
    def __init__(self):
        self.evidence = []          # [{"id", "tool", "input", "result"}]

    def tool_schemas(self):
        out = []
        for name in TOOL_FUNCS:
            schema = copy.deepcopy(CONTRACT["tools"][name]["request"])
            schema.pop("additionalProperties", None)
            out.append({"name": name, "description": DESCRIPTIONS[name], "input_schema": schema})
        return out

    def call(self, name, args):
        """Returns (payload, evidence_id or None). Never raises."""
        if name not in TOOL_FUNCS:
            return ToolError("NO_DATA", f"tool '{name}' is not available").payload(), None
        try:
            result = TOOL_FUNCS[name](args)
        except ToolError as e:
            return e.payload(), None
        except KeyError as e:
            return ToolError("BAD_PERIOD", f"missing required argument: {e}").payload(), None
        except Exception as e:  # noqa: BLE001
            return ToolError("INTERNAL", f"{type(e).__name__}: {str(e)[:200]}").payload(), None
        if isinstance(result, dict) and "error" in result:          # tools that RETURN errors instead of raising
            err = result["error"] if isinstance(result["error"], dict) else {"message": str(result["error"])}
            code = err.get("code") if err.get("code") in ERROR_CODES else "NO_DATA"
            return {"error": {"code": code, "message": f"{err.get('code', '')}: {err.get('message', '')}"}}, None
        eid = f"E{len(self.evidence) + 1}"
        payload = {"evidence_id": eid, **result}
        errs = [e.message for e in Draft7Validator(CONTRACT["tools"][name]["response"]).iter_errors(payload)]
        if errs:
            return ToolError("INTERNAL", f"tool output violates the contract: {errs[0]}").payload(), None
        self.evidence.append({"id": eid, "tool": name, "input": args, "result": payload})
        return payload, eid


if __name__ == "__main__":
    r = Runner()
    P = {"start": "2011-09-26", "end": "2011-11-20"}
    base = {"metric": "net_revenue", "period": P, "compare_to": "same_period_last_year"}
    calls = [("detect_anomaly", base),
             ("drill_down", {**base, "dimensions": ["country", "customer_type"], "top_k": 5}),
             ("driver_decomposition", base), ("customer_movement", base),
             ("significance_test", {**base, "segment": {"dimension": "customer_type", "value": "wholesale_like"}}),
             ("drill_down", {**base, "metric": "active_customers", "dimensions": ["country"]})]   # must be a clean error
    print("tools offered:", list(TOOL_FUNCS))
    for n, a in calls:
        res, eid = r.call(n, a)
        print(f"{eid or 'ERR':4s} {n:22s}", res.get("error") or "ok")
    from confidence import compute_confidence
    print("confidence:", json.dumps(compute_confidence(r.evidence)))