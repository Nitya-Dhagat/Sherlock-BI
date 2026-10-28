"""
significance_test: is a segment's change distinguishable from normal variation?

Customer-level bootstrap: resample the segment's customers (the customer is the independent unit, not the order)
and recompute the relative change of the metric between the baseline and current windows.
  effect_size = (current - baseline) / baseline          (fraction; -0.15 = -15%)
  CI          = 2.5th / 97.5th percentile of 2000 bootstrap effects (seed fixed, so results are reproducible)
  p_value     = two-sided share of bootstrap effects on the other side of zero
Strength labels are fixed constants (never chosen by the LLM):
  supporting : CI excludes 0 and |effect| >= 5%      weak : CI excludes 0 (small effect) or p < 0.10      none : otherwise
Selection caveat: the agent picks the segment after looking at the data, so p-values are optimistic.
Metrics: net_revenue, gross_revenue.  Segment dimensions: country, uk_vs_international, customer_type, tenure, rfm_tier.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
from common import (DEFINITIONS, ToolError, add_segments, apply_filters, d, get_con, load_lines,  # noqa: E402
                    prepare_period, uses_customer_dims, CUSTOMER_DIMS, LINE_DIMS)

N_BOOT, SEED, STRONG_EFFECT, WEAK_P, MIN_CUSTOMERS = 2000, 42, 0.05, 0.10, 10
CAVEAT = ("Segment was chosen after looking at the data, so p-values are optimistic; "
          "rely on the interval and effect size. Customer-level bootstrap, 2000 resamples.")


def significance_test(request):
    con = get_con()
    metric = request["metric"]
    if metric not in ("net_revenue", "gross_revenue"):
        raise ToolError("NO_DATA", "significance_test supports net_revenue and gross_revenue")
    dim, val = request["segment"]["dimension"], request["segment"]["value"]
    if dim not in LINE_DIMS + CUSTOMER_DIMS:
        raise ToolError("NO_DATA", f"unsupported segment dimension: {dim}")
    if val in ("unattributed",):
        raise ToolError("NO_DATA", "unattributed revenue has no customer_id, so a customer-level test is impossible")
    p = prepare_period(con, request["period"], request.get("compare_to", "same_period_last_year"))
    lines = load_lines(con, min(p["s"], p["bs"]), max(p["e"], p["be"]))
    if dim in CUSTOMER_DIMS or uses_customer_dims(None, request.get("filters")):
        lines = add_segments(lines, con, p["s"])
    lines = apply_filters(lines, request.get("filters"))
    seg = lines[(lines[dim] == val) & lines["customer_id"].notna()]
    if seg.empty:
        raise ToolError("EMPTY_SLICE", f"no customers in segment {dim} = {val}")
    cur = seg[(seg["sale_date"] >= p["s"]) & (seg["sale_date"] <= p["e"])]
    base = seg[(seg["sale_date"] >= p["bs"]) & (seg["sale_date"] <= p["be"])]

    def rev(df):
        df = df if metric == "net_revenue" else df[~df["is_cancel"]]
        return df.groupby("customer_id")["line_revenue"].sum()

    rc, rb = rev(cur), rev(base)
    ids = rc.index.union(rb.index)
    n = len(ids)
    if n < MIN_CUSTOMERS:
        raise ToolError("TINY_BASE", f"only {n} customers in the segment; too few for a bootstrap")
    P, B = rc.reindex(ids, fill_value=0.0).to_numpy(), rb.reindex(ids, fill_value=0.0).to_numpy()
    sb = float(B.sum())
    if sb <= 0:
        raise ToolError("TINY_BASE", "segment has no baseline revenue")
    effect = (float(P.sum()) - sb) / sb
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, n, size=(N_BOOT, n))
    bp, bb = P[idx].sum(axis=1), B[idx].sum(axis=1)
    ok = bb > 0
    eff = bp[ok] / bb[ok] - 1
    lo, hi = (float(x) for x in np.percentile(eff, [2.5, 97.5]))
    pval = max(2 * min(float((eff >= 0).mean()), float((eff <= 0).mean())), 1.0 / len(eff))
    excludes = lo > 0 or hi < 0
    strength = ("supporting" if excludes and abs(effect) >= STRONG_EFFECT else
                "weak" if excludes or pval < WEAK_P else "none")
    return {"method": "customer-level bootstrap, 2000 resamples", "effect_size": round(effect, 4),
            "ci_low": round(lo, 4), "ci_high": round(hi, 4), "p_value": round(min(pval, 1.0), 4),
            "strength": strength, "n_customers": int(n), "effect_pct": round(effect * 100, 1),
            "segment": {"dimension": dim, "value": val}, "caveat": CAVEAT,
            "period_used": {"start": d(p["s"]), "end": d(p["e"])},
            "baseline": {"start": d(p["bs"]), "end": d(p["be"])},
            "definition": DEFINITIONS[metric] + "; segment membership fixed as of period start",
            "data_through": d(p["dt"])}


if __name__ == "__main__":
    req = {"metric": "net_revenue", "period": {"start": "2011-09-26", "end": "2011-11-20"},
           "compare_to": "same_period_last_year", "segment": {"dimension": "customer_type", "value": "wholesale_like"}}
    try:
        print(json.dumps(significance_test(req), indent=2))
    except ToolError as err:
        print(json.dumps(err.payload(), indent=2))