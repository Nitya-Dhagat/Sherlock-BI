"""detect_anomaly: is the metric really off in this period, and how unusual is it?

Method (documented so the agent and the judges can describe it):
1. Compare the period with its baseline (same period last year by default).
2. For additive metrics, scale the baseline by trading days (a week with 4 trading
   days must not look like a drop against a week with 5).
3. Build a history of the same comparison for earlier windows of the same length,
   stepping weekly, and score today's adjusted change against that history with a
   robust z-score (median / MAD).
4. Severity comes from the size of the adjusted change (5% low, 10% medium, 20% high).
   If the z-score is under 2 in size the change is within normal variation and severity
   is capped at "low"; if it is 3 or more the severity goes up one level. With too little
   history for a z-score, severity uses size alone.
The incomplete final period is refused, never flagged.
"""
from __future__ import annotations

import pandas as pd

from .common import (
    ADDITIVE_METRICS, DEFINITIONS, METRICS, Period, ToolError, check_metric, data_bounds,
    filter_slice, guard_periods, make_resolver, metrics, parse_period, pct_change, prepare, r,
    robust_z, slice_period,
)

MIN_HISTORY = 8


def _compare(metric: str, d_cur: pd.DataFrame, d_base: pd.DataFrame) -> dict:
    mc, mb = metrics(d_cur), metrics(d_base)
    actual, expected = mc[metric], mb[metric]
    adjusted = metric in ADDITIVE_METRICS
    if adjusted and mb["trading_days"]:
        expected_adj = expected * mc["trading_days"] / mb["trading_days"]
    else:
        expected_adj = expected
    return {
        "actual": actual, "expected": expected, "expected_adj": expected_adj, "adjusted": adjusted,
        "pct": pct_change(actual, expected), "pct_adj": pct_change(actual, expected_adj),
        "td_cur": mc["trading_days"], "td_base": mb["trading_days"],
    }


def _history(df, metric, cur: Period, resolver, filters) -> list[float]:
    """Adjusted % changes for earlier windows of the same length (weekly steps, ending before `cur`)."""
    first, last = data_bounds(df)
    length = cur.days
    weeks_back = (cur.start - first).days // 7
    out: list[float] = []
    for k in range(weeks_back, 0, -1):
        w_start = cur.start - pd.Timedelta(days=7 * k)
        w = Period(w_start, w_start + pd.Timedelta(days=length - 1))
        if w.end >= cur.start:
            continue
        b = resolver(w)
        if b.start < first or b.end > last:
            continue
        d_w = filter_slice(slice_period(df, w), filters, w)
        d_b = filter_slice(slice_period(df, b), filters, b)
        if d_w.empty or d_b.empty:
            continue
        pct = _compare(metric, d_w, d_b)["pct_adj"]
        if pct is not None:
            out.append(pct)
    return out


def _severity(pct_adj: float, z: float | None) -> str:
    levels = ["none", "low", "medium", "high"]
    size = abs(pct_adj)
    level = 3 if size >= 20 else 2 if size >= 10 else 1 if size >= 5 else 0
    if z is not None:
        if abs(z) < 2:
            level = min(level, 1)  # history shows swings this size are normal: never above "low"
        elif abs(z) >= 3 and level > 0:
            level = min(3, level + 1)
    return levels[level]


def detect_anomaly(df, metric: str, period: dict, compare_to="same_period_last_year", filters=None) -> dict:
    df = prepare(df)
    check_metric(metric, METRICS)
    cur = parse_period(period)
    resolver, base, mode = make_resolver(cur, compare_to)
    guard_periods(df, cur, base)

    d_cur = filter_slice(slice_period(df, cur), filters, cur)
    d_base = filter_slice(slice_period(df, base), filters, base)
    if d_cur.empty:
        raise ToolError("EMPTY_SLICE", "No transactions in the requested period (after filters).", period=cur.to_dict())
    if d_base.empty:
        raise ToolError("EMPTY_SLICE", "No transactions in the baseline period (after filters).", baseline=base.to_dict())

    c = _compare(metric, d_cur, d_base)
    if c["pct_adj"] is None:
        raise ToolError("ZERO_BASELINE", "Baseline value is zero, so a percentage change is undefined.", baseline=base.to_dict())

    history = _history(df, metric, cur, resolver, filters)
    z = robust_z(c["pct_adj"], history, MIN_HISTORY)
    severity = _severity(c["pct_adj"], z)
    direction = "flat" if severity == "none" else ("drop" if c["pct_adj"] < 0 else "rise")

    method = f"{mode}" + ("+trading_day_adjusted" if c["adjusted"] else "")
    method += f"+robust_z(history={len(history)} windows)" if z is not None else "+no_z(history too short)"

    return {
        "metric": metric,
        "period": cur.to_dict(),
        "compare_to_period": base.to_dict(),
        "actual": r(c["actual"]),
        "expected": r(c["expected"]),
        "expected_trading_day_adjusted": r(c["expected_adj"]),
        "change_pct": r(c["pct"]),
        "change_pct_adjusted": r(c["pct_adj"]),
        "z_score": r(z),
        "severity": severity,
        "direction": direction,
        "is_anomaly": severity in ("medium", "high"),
        "method": method,
        "trading_days": {"actual": c["td_cur"], "expected": c["td_base"]},
        "history_windows": len(history),
        "filters": filters or [],
        "definition": DEFINITIONS[metric],
        "data_through": data_bounds(df)[1].strftime("%Y-%m-%d"),
    }