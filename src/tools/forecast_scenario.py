"""
WhyBot — src/tools/forecast_scenario.py   (v2: real schema)

Two projections over the same horizon:
  baseline : current behaviour continues
  what_if  : the identified segment returns to its level 52 weeks earlier

Method: seasonal-naive on the 52-week lag, scaled by a damped recent year-on-year
ratio. Two years of weekly data (~105 points) cannot honestly support Prophet or a
seasonal ARIMA, and fancy baselines are third on the playbook's cut list, so this
stays small on purpose.

Everything returned carries an explicit caveat. It illustrates magnitude; it is not
a forecast anyone should plan against.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from _segments import (UnknownDimension, filters_sql, lines_cte, not_filters_sql,
                       revenue_expr)

DEFAULT_HORIZON = 12
YOY_WINDOW = 8
DAMPING = 0.9
MIN_WEEKS = 60

CAVEAT = (
    "Illustrative projection from two years of weekly history using a seasonal-naive "
    "baseline. It assumes no new shocks, no price or product changes, and that last "
    "year's seasonal shape repeats. Treat the gap between the two lines as a rough "
    "order of magnitude, not a committed number."
)


def _d(s):
    return datetime.fromisoformat(str(s)[:10]).date()


def _weekly(con, as_of, filters=None, exclude=None, metric="net_revenue"):
    """Weekly revenue from product_lines.week_start. `exclude` removes a segment."""
    where, params = filters_sql(filters)
    nwhere, nparams = not_filters_sql(exclude)
    sql = f"""
        WITH {lines_cte(as_of)}
        SELECT week_start, {revenue_expr(metric)} AS rev
        FROM lines WHERE TRUE {where} {nwhere}
        GROUP BY 1 ORDER BY 1
    """
    rows = con.execute(sql, [*params, *nparams]).fetchall()
    return [(str(r[0])[:10], float(r[1])) for r in rows]


def _drop_partial_tail(series, data_through=None):
    """Never project from an incomplete final week (data ends 2011-12-09, a Friday)."""
    if not series:
        return series
    last = _d(series[-1][0])
    end = _d(data_through) if data_through else last + timedelta(days=6)
    return series[:-1] if (end - last).days < 6 else series


def _project(series, horizon, yoy_window=YOY_WINDOW):
    if len(series) < MIN_WEEKS:
        return None, "insufficient_history"
    dates = [_d(d) for d, _ in series]
    vals = np.array([v for _, v in series], dtype=float)

    recent = vals[-yoy_window:]
    lagged = vals[-yoy_window - 52:-52] if len(vals) >= yoy_window + 52 else None
    ratio = 1.0 if (lagged is None or lagged.sum() <= 0) \
        else float(recent.sum() / lagged.sum())
    ratio = float(np.clip(ratio, 0.3, 3.0))

    out, last = [], dates[-1]
    for h in range(1, horizon + 1):
        lag_idx = len(vals) - 52 + h - 1
        base = vals[lag_idx] if 0 <= lag_idx < len(vals) else float(recent.mean())
        damped = 1 + (ratio - 1) * (DAMPING ** h)
        out.append({"period_start": str(last + timedelta(weeks=h)),
                    "value": round(float(base * damped), 2)})
    return out, f"seasonal_naive_yoy(ratio={ratio:.3f}, damping={DAMPING})"


def forecast_scenario(request: dict, con=None) -> dict:
    """
    request = {"horizon_weeks": 12, "filters": [],
               "recover_segment": [{"dimension":"customer_type","value":"wholesale_like"}],
               "as_of": "2010-09-27", "history_weeks": 40,
               "data_through": "2011-12-09"}
    """
    try:
        horizon = int(request.get("horizon_weeks", DEFAULT_HORIZON))
        filters = request.get("filters") or []
        seg = request.get("recover_segment") or []
        hist_weeks = int(request.get("history_weeks", 40))
        data_through = request.get("data_through", "2011-12-09")
        as_of = request.get("as_of", "2010-09-27")

        full = _drop_partial_tail(_weekly(con, as_of, filters), data_through)
        if len(full) < MIN_WEEKS:
            return {"error": {"code": "INSUFFICIENT_HISTORY",
                              "message": f"{len(full)} complete weeks; need {MIN_WEEKS}+."}}

        baseline, method = _project(full, horizon)
        if baseline is None:
            return {"error": {"code": "INSUFFICIENT_HISTORY", "message": method}}

        what_if = seg_note = None
        if seg:
            ex = _drop_partial_tail(_weekly(con, as_of, filters, exclude=seg),
                                    data_through)
            rest, _ = _project(ex, horizon)
            if rest is None:
                return {"error": {"code": "INSUFFICIENT_HISTORY",
                                  "message": "not enough history excluding the segment"}}
            full_map, ex_map = dict(full), dict(ex)
            seg_hist = {_d(d) + timedelta(weeks=52): full_map[d] - ex_map.get(d, 0.0)
                        for d in full_map}
            what_if = [{"period_start": p["period_start"],
                        "value": round(p["value"] + seg_hist.get(_d(p["period_start"]), 0.0), 2)}
                       for p in rest]
            seg_note = ("what_if holds the rest of the business on its own baseline path "
                        "and restores the segment to its level 52 weeks earlier")

        gap = round(sum(p["value"] for p in what_if) - sum(p["value"] for p in baseline), 2) \
            if what_if else None

        # A negative gap means the segment was NOT depressed relative to a year ago —
        # "restoring" it makes things worse. Say so rather than letting the UI label it
        # "if the segment recovers".
        gap_note = None
        if gap is not None and gap <= 0:
            gap_note = ("The segment's level 52 weeks earlier is not higher than its "
                        "projected level, so this is not a recovery scenario. Pick a "
                        "segment that actually declined, or read this as the cost of "
                        "reverting to last year.")

        return {
            "horizon_weeks": horizon, "method": method,
            "history": [{"period_start": d, "value": round(v, 2)}
                        for d, v in full[-hist_weeks:]],
            "baseline": baseline, "what_if": what_if,
            "recover_segment": seg, "what_if_note": seg_note,
            "customer_type_as_of": as_of,
            "cumulative_gap": gap, "cumulative_gap_note": gap_note,
            "baseline_total": round(sum(p["value"] for p in baseline), 2),
            "what_if_total": round(sum(p["value"] for p in what_if), 2) if what_if else None,
            "caveat": CAVEAT,
            "definition": "weekly net_revenue from product_lines, cancellations negative",
        }
    except UnknownDimension as e:
        return {"error": {"code": "UNKNOWN_DIMENSION", "message": str(e)}}
    except Exception as e:
        return {"error": {"code": "TOOL_FAILED", "message": str(e)[:300]}}
