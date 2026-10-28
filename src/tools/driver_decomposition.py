"""
driver_decomposition: HOW did revenue change? (customers vs orders per customer vs order value vs unattributed)

Attributed revenue R = C (active customers) x P (orders per customer) x A (revenue per order).
LMDI (log-mean) decomposition, so the three effects are order-independent and add up exactly:
    effect_x = L * ln(x1 / x0),   L = (R1 - R0) / ln(R1 / R0)
Revenue without a customer_id is reported as a separate fourth term (its raw change).
    total_change = active_customers + orders_per_customer + aov + unattributed   (checked in code)
Metrics: net_revenue (attributed incl. cancellations) or gross_revenue (attributed, excl. cancellations).
Orders / active customers always count non-cancellation lines with a customer_id.
Run:  python src/tools/driver_decomposition.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
from common import (DEFINITIONS, ToolError, add_segments, apply_filters, d, get_con, load_lines,  # noqa: E402
                    prepare_period, uses_customer_dims)


def _stats(df, metric):
    att, un = df[df["customer_id"].notna()], df[df["customer_id"].isna()]
    g = att[~att["is_cancel"]]
    if metric == "net_revenue":
        rev, unrev = att["line_revenue"].sum(), un["line_revenue"].sum()
    else:
        rev, unrev = g["line_revenue"].sum(), un[~un["is_cancel"]]["line_revenue"].sum()
    return {"R": float(rev), "U": float(unrev), "C": int(g["customer_id"].nunique()),
            "O": int(g["invoice"].nunique())}


def driver_decomposition(request):
    con = get_con()
    metric = request["metric"]
    if metric not in ("net_revenue", "gross_revenue"):
        raise ToolError("NO_DATA", "driver_decomposition supports net_revenue and gross_revenue")
    p = prepare_period(con, request["period"], request.get("compare_to", "same_period_last_year"))
    lines = load_lines(con, min(p["s"], p["bs"]), max(p["e"], p["be"]))
    if uses_customer_dims(None, request.get("filters")):
        lines = add_segments(lines, con, p["s"])
    lines = apply_filters(lines, request.get("filters"))
    cur = lines[(lines["sale_date"] >= p["s"]) & (lines["sale_date"] <= p["e"])]
    base = lines[(lines["sale_date"] >= p["bs"]) & (lines["sale_date"] <= p["be"])]
    a, b = _stats(base, metric), _stats(cur, metric)       # a = baseline (0), b = current (1)
    if min(a["R"], b["R"], a["C"], b["C"], a["O"], b["O"]) <= 0:
        raise ToolError("TINY_BASE", "a window has no attributed revenue, customers or orders; "
                                     "the decomposition is undefined")
    dr = b["R"] - a["R"]
    L = dr / np.log(b["R"] / a["R"]) if abs(dr) > 1e-9 else a["R"]
    e_c = L * np.log(b["C"] / a["C"])
    e_p = L * np.log((b["O"] / b["C"]) / (a["O"] / a["C"]))
    e_a = L * np.log((b["R"] / b["O"]) / (a["R"] / a["O"]))
    e_u = b["U"] - a["U"]
    total = dr + e_u
    if abs(e_c + e_p + e_a + e_u - total) > 0.01:
        raise ToolError("INTERNAL", "effects do not sum to the total change")

    def eff(x):
        return {"change": round(float(x), 2), "share": round(float(x / total), 4) if abs(total) > 1e-9 else 0.0}

    def lvl(s):
        return {"active_customers": s["C"], "orders": s["O"], "orders_per_customer": round(s["O"] / s["C"], 3),
                "aov": round(s["R"] / s["O"], 2), "attributed_revenue": round(s["R"], 2),
                "unattributed_revenue": round(s["U"], 2)}

    return {"total_change": round(float(total), 2),
            "effects": {"active_customers": eff(e_c), "orders_per_customer": eff(e_p),
                        "aov": eff(e_a), "unattributed": eff(e_u)},
            "sum_check": round(float(e_c + e_p + e_a + e_u), 2),
            "levels": {"baseline": lvl(a), "current": lvl(b)},
            "period_used": {"start": d(p["s"]), "end": d(p["e"])},
            "baseline": {"start": d(p["bs"]), "end": d(p["be"])},
            "partial_period_excluded": p["partial"],
            "definition": DEFINITIONS[metric] + "; attributed revenue = customers x orders/customer x revenue/order "
                                                "(log-mean decomposition); unattributed reported separately",
            "data_through": d(p["dt"])}


if __name__ == "__main__":
    req = {"metric": "net_revenue", "period": {"start": "2011-09-26", "end": "2011-11-20"},
           "compare_to": "same_period_last_year"}
    try:
        print(json.dumps(driver_decomposition(req), indent=2))
    except ToolError as err:
        print(json.dumps(err.payload(), indent=2))