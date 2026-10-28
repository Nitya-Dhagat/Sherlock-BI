"""
drill_down: where did the change happen?

Splits the change in an ADDITIVE metric (net_revenue, gross_revenue, orders) across dimension values.
Every slice is returned in full (top_k rows + one "(other)" row), so the rows sum exactly to total_change.
  share_of_net_change    = slice change / total_change (can exceed 1 or be negative when some slices grow)
  share_of_gross_decline = slice change / sum of all NEGATIVE slice changes (0 for growing slices)
Dimensions: country, uk_vs_international, customer_type, tenure, rfm_tier  (category: not available yet)
Customer segments are fixed as of the current period start; customers without a customer_id are "unattributed".
Run:  python src/tools/drill_down.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402
from common import (ADDITIVE, CUSTOMER_DIMS, DEFINITIONS, ToolError, add_segments, apply_filters,  # noqa: E402
                    d, get_con, load_lines, metric_by, metric_value, prepare_period, uses_customer_dims)


def _row(value, change, base, share_net, share_gross, n_customers):
    return {"value": str(value), "change": round(float(change), 2),
            "pct_change": round(float(change / base * 100), 1) if base > 0 else 100.0,
            "share_of_net_change": round(float(share_net), 4),
            "share_of_gross_decline": round(float(share_gross), 4),
            "n_customers": int(n_customers)}


def drill_down(request):
    con = get_con()
    metric = request["metric"]
    if metric not in ADDITIVE:
        raise ToolError("NO_DATA", "drill_down supports net_revenue, gross_revenue and orders; "
                                   "use driver_decomposition for customers and AOV")
    dims = list(request.get("dimensions") or ["country"])
    top_k = int(request.get("top_k", 5))
    p = prepare_period(con, request["period"], request.get("compare_to", "same_period_last_year"))

    skipped = [x for x in dims if x == "category"]
    dims = [x for x in dims if x != "category"]
    if not dims:
        raise ToolError("NO_DATA", "no usable dimensions (category is not available yet)")

    lines = load_lines(con, min(p["s"], p["bs"]), max(p["e"], p["be"]))
    if uses_customer_dims(dims, request.get("filters")):
        lines = add_segments(lines, con, p["s"])
    lines = apply_filters(lines, request.get("filters"))
    cur = lines[(lines["sale_date"] >= p["s"]) & (lines["sale_date"] <= p["e"])]
    base = lines[(lines["sale_date"] >= p["bs"]) & (lines["sale_date"] <= p["be"])]
    if cur.empty and base.empty:
        raise ToolError("EMPTY_SLICE", "no product lines in either window for these filters")
    both = pd.concat([cur, base])

    total_change = metric_value(cur, metric) - metric_value(base, metric)
    out, gross_by_dim = {}, {}
    for dim in dims:
        c, b = metric_by(cur, dim, metric), metric_by(base, dim, metric)
        idx = c.index.union(b.index)
        c, b = c.reindex(idx, fill_value=0.0), b.reindex(idx, fill_value=0.0)
        change = c - b
        if abs(change.sum() - total_change) > max(0.01, 1e-6 * abs(total_change)):
            raise ToolError("INTERNAL", f"slices for {dim} do not sum to the total change")
        gross_decline = float(change[change < 0].sum())
        gross_by_dim[dim] = round(gross_decline, 2)
        ncust = both.groupby(dim)["customer_id"].nunique()
        order = change.sort_values(ascending=total_change < 0)
        rows = []
        for value, ch in order.head(top_k).items():
            rows.append(_row(value, ch, b[value], ch / total_change if total_change else 0.0,
                             ch / gross_decline if (ch < 0 and gross_decline < 0) else 0.0, ncust.get(value, 0)))
        rest = order.iloc[top_k:]
        if len(rest):
            ch, bs_ = float(rest.sum()), float(b[rest.index].sum())
            rows.append(_row("(other)", ch, bs_, ch / total_change if total_change else 0.0,
                             float(rest[rest < 0].sum()) / gross_decline if gross_decline < 0 else 0.0,
                             both[both[dim].isin(rest.index)]["customer_id"].nunique()))
        out[dim] = rows

    return {"total_change": round(total_change, 2), "gross_decline": gross_by_dim[dims[0]],
            "gross_decline_by_dimension": gross_by_dim, "contributors": out, "skipped_dimensions": skipped,
            "period_used": {"start": d(p["s"]), "end": d(p["e"])},
            "baseline": {"start": d(p["bs"]), "end": d(p["be"])},
            "partial_period_excluded": p["partial"],
            "definition": DEFINITIONS[metric] + "; customer segments fixed as of period start",
            "data_through": d(p["dt"])}


if __name__ == "__main__":
    req = {"metric": "net_revenue", "period": {"start": "2011-09-26", "end": "2011-11-20"},
           "compare_to": "same_period_last_year",
           "dimensions": ["country", "uk_vs_international", "customer_type", "tenure"], "top_k": 5}
    try:
        print(json.dumps(drill_down(req), indent=2))
    except ToolError as err:
        print(json.dumps(err.payload(), indent=2))