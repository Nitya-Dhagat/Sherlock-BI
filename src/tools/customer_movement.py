"""
customer_movement: WHO changed? retained / lost / new customers (+ unattributed revenue).

Active = at least one non-cancellation product line with a customer_id in the window.
  lost     : active in the baseline window, NOT active in the current window nor in the grace days after it
             (lost-check window = [period start, period start + max(window_days, period length) - 1], capped at
             the last complete week). Default window_days = 90 (the EDA: 29% of order gaps exceed 60 days).
  new      : active in the current window, not active in the baseline window
  retained : everyone else with lines in either window
Revenue change per group = current-window revenue minus baseline-window revenue of its customers.
    total_change = retained + lost + new + unattributed   (checked in code)
Concentration: share of the decline (or growth) carried by the top 3 / top 10 customers;
concentrated_in_few_accounts = top 3 carry >= 50%.
Metrics: net_revenue (default) or gross_revenue.
Run:  python src/tools/customer_movement.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402
from common import (DAY, DEFINITIONS, ToolError, add_segments, apply_filters, d, get_con,  # noqa: E402
                    load_lines, prepare_period, uses_customer_dims)

CONCENTRATION_TOP3 = 0.5


def customer_movement(request):
    con = get_con()
    metric = request.get("metric", "net_revenue")
    if metric not in ("net_revenue", "gross_revenue"):
        raise ToolError("NO_DATA", "customer_movement supports net_revenue and gross_revenue")
    window_days = int(request.get("window_days", 90))
    p = prepare_period(con, request["period"], request.get("compare_to", "same_period_last_year"))
    ext_end = min(p["lf"], p["s"] + (max(window_days, p["L"]) - 1) * DAY)
    lines = load_lines(con, min(p["s"], p["bs"]), max(p["e"], p["be"], ext_end))
    if uses_customer_dims(None, request.get("filters")):
        lines = add_segments(lines, con, p["s"])
    lines = apply_filters(lines, request.get("filters"))
    P = lines[(lines["sale_date"] >= p["s"]) & (lines["sale_date"] <= p["e"])]
    B = lines[(lines["sale_date"] >= p["bs"]) & (lines["sale_date"] <= p["be"])]
    X = lines[(lines["sale_date"] >= p["s"]) & (lines["sale_date"] <= ext_end)]
    if P.empty and B.empty:
        raise ToolError("EMPTY_SLICE", "no product lines in either window for these filters")

    def active(df):
        return set(df[df["customer_id"].notna() & ~df["is_cancel"]]["customer_id"].unique())

    def rev(df):
        df = df if metric == "net_revenue" else df[~df["is_cancel"]]
        return df.groupby("customer_id")["line_revenue"].sum()      # NaN customer_id rows are dropped

    def unattr(df):
        u = df[df["customer_id"].isna()]
        return float((u if metric == "net_revenue" else u[~u["is_cancel"]])["line_revenue"].sum())

    act_b, act_p, act_x = active(B), active(P), active(X)
    ids = set(P["customer_id"].dropna()) | set(B["customer_id"].dropna())
    lost = act_b - act_x
    new = act_p - act_b
    retained = ids - lost - new
    rp, rb = rev(P), rev(B)
    change = rp.reindex(sorted(ids), fill_value=0.0) - rb.reindex(sorted(ids), fill_value=0.0)
    un_change = unattr(P) - unattr(B)
    total = float((P if metric == "net_revenue" else P[~P["is_cancel"]])["line_revenue"].sum()
                  - (B if metric == "net_revenue" else B[~B["is_cancel"]])["line_revenue"].sum())

    def group(s):
        ch = float(change[list(s)].sum()) if s else 0.0
        return {"customers": len(s), "revenue_change": round(ch, 2),
                "share_of_total_change": round(ch / total, 4) if abs(total) > 1e-9 else 0.0}

    g_ret, g_lost, g_new = group(retained), group(lost), group(new)
    if abs(g_ret["revenue_change"] + g_lost["revenue_change"] + g_new["revenue_change"] + un_change - total) > 0.05:
        raise ToolError("INTERNAL", "groups do not sum to the total change")

    pool = change[change < 0] if total < 0 else change[change > 0]
    pool = pool.reindex(pool.abs().sort_values(ascending=False).index)
    tot_abs = float(pool.abs().sum())
    top3 = float(pool.head(3).abs().sum() / tot_abs) if tot_abs else 0.0
    top10 = float(pool.head(10).abs().sum() / tot_abs) if tot_abs else 0.0
    lost_series = change[list(lost)].sort_values().head(10) if lost else pd.Series(dtype=float)
    eff_days = (ext_end - p["s"]).days + 1

    out = {"window_days": int(eff_days), "total_change": round(total, 2),
           "retained": g_ret, "lost": g_lost, "new": g_new,
           "unattributed": {"revenue_change": round(un_change, 2),
                            "share_of_total_change": round(un_change / total, 4) if abs(total) > 1e-9 else 0.0},
           "top10_concentration": round(top10, 4), "top3_concentration": round(top3, 4),
           "concentrated_in_few_accounts": bool(top3 >= CONCENTRATION_TOP3),
           "top_lost_accounts": [{"customer_id": int(i), "revenue_change": round(float(v), 2)}
                                 for i, v in lost_series.items()],
           "lost_check_window": {"start": d(p["s"]), "end": d(ext_end)},
           "period_used": {"start": d(p["s"]), "end": d(p["e"])},
           "baseline": {"start": d(p["bs"]), "end": d(p["be"])},
           "partial_period_excluded": p["partial"],
           "definition": DEFINITIONS[metric] + "; lost = active in baseline, none in the lost-check window; "
                                               "new = active now, not in baseline"
           , "data_through": d(p["dt"])}
    if eff_days < 60:
        out["warning"] = (f"lost-check window is only {eff_days} days; periodic buyers may look lost "
                          "(29% of order gaps exceed 60 days)")
    return out


if __name__ == "__main__":
    req = {"metric": "net_revenue", "period": {"start": "2011-09-26", "end": "2011-11-20"},
           "compare_to": "same_period_last_year", "window_days": 90}
    try:
        print(json.dumps(customer_movement(req), indent=2))
    except ToolError as err:
        print(json.dumps(err.payload(), indent=2))