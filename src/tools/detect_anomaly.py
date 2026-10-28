"""
detect_anomaly: is the drop real, or just seasonality / fewer trading days?

Metrics: net_revenue, gross_revenue, orders, aov.   Filters: country, uk_vs_international only.
Two baselines, each with a z-score from history (so the agent can see whether both agree):
  yoy : this window vs the same window 364 days earlier. For additive metrics the expectation is scaled by
        trading days (days with sales). z = how unusual this change is versus the year-over-year changes of
        all other windows of the same length (windows overlapping the target are excluded; needs >= 8).
  stl : weekly series (partial first/last weeks dropped) with the target weeks masked, then STL (period 52,
        robust). Expected = trend + seasonal of those weeks; z = summed residual / (weekly residual sd * sqrt(weeks)).
        Needs a window of at least one full ISO week (Mon-Sun).
Primary method: yoy when compare_to is same_period_last_year, otherwise stl (seasonality makes a plain
previous-period comparison misleading, so it is never used as the expectation).
The incomplete final week is never analysed (the period end is clamped and partial_period_excluded = true).
Severity (fixed in code): |z| >= 2 high, >= 1.5 medium, >= 1 low, else none; |change| < 2% is always none.
Run:  python src/tools/detect_anomaly.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from common import (ADDITIVE, DAY, DEFINITIONS, ToolError, d, daily_frame, get_con,  # noqa: E402
                    prepare_period)

MIN_HIST_WINDOWS = 8


def window_value(daily, s, e, metric):
    w = daily.loc[s:e]
    days = int(w["trading_day"].sum())
    if metric == "aov":
        n = float(w["orders"].sum())
        return (float(w["gross_revenue"].sum()) / n if n else float("nan")), days
    return float(w[metric].sum()), days


def yoy_stat(daily, s, e, metric):
    a, ta = window_value(daily, s, e, metric)
    b, tb = window_value(daily, s - 364 * DAY, e - 364 * DAY, metric)
    adjusted = metric in ADDITIVE and ta > 0 and tb > 0
    expected = b * ta / tb if adjusted else b
    if not expected or np.isnan(expected) or np.isnan(a):
        return None
    return {"actual": a, "expected": expected, "pct": (a / expected - 1) * 100,
            "ta": ta, "tb": tb, "adjusted": adjusted}


def yoy_check(daily, s, e, L, metric, ds, lf):
    target = yoy_stat(daily, s, e, metric)
    if target is None:
        raise ToolError("NO_DATA", "no baseline data for the same window last year")
    hist = []
    for st in pd.date_range(ds + 364 * DAY, lf - (L - 1) * DAY, freq="7D"):
        en = st + (L - 1) * DAY
        if st <= e and en >= s:                 # overlaps the target window
            continue
        r = yoy_stat(daily, st, en, metric)
        if r is not None:
            hist.append(r["pct"])
    if len(hist) < MIN_HIST_WINDOWS:
        raise ToolError("TINY_BASE", f"only {len(hist)} comparable historical windows; "
                                     "use a shorter period to estimate normal variation")
    sd = float(np.std(hist, ddof=1))
    z = (target["pct"] - float(np.mean(hist))) / sd if sd > 0 else 0.0
    target.update(z=z, hist_n=len(hist), hist_mean=float(np.mean(hist)), hist_sd=sd)
    return target


def stl_check(daily, s, e, metric, ds, lf):
    from statsmodels.tsa.seasonal import STL
    ws = daily.index - pd.to_timedelta(daily.index.dayofweek, unit="D")
    wk = daily[["net_revenue", "gross_revenue", "orders"]].groupby(ws).sum()
    wk = wk[(wk.index >= ds) & (wk.index <= lf - 6 * DAY)]
    ser = wk["gross_revenue"] / wk["orders"].replace(0, np.nan) if metric == "aov" else wk[metric]
    ser = ser.astype(float)
    in_win = (ser.index >= s) & (ser.index + 6 * DAY <= e)
    n = int(in_win.sum())
    if n == 0 or len(ser) < 104:
        return None
    work = ser.copy()
    work[in_win] = np.nan
    work = work.interpolate(limit_direction="both")
    work.index = pd.DatetimeIndex(work.index, freq="7D")
    res = STL(work, period=52, robust=True).fit()
    fitted = (res.trend + res.seasonal)
    resid = (work - fitted)[~in_win]
    sd = float(resid.std(ddof=1))
    if not sd or np.isnan(sd):
        return None
    act, exp_ = ser[in_win].to_numpy(), fitted[in_win].to_numpy()
    if metric in ADDITIVE:
        actual, expected = float(act.sum()), float(exp_.sum())
        z = (actual - expected) / (sd * np.sqrt(n))
    else:
        actual, expected = float(act.mean()), float(exp_.mean())
        z = (actual - expected) / (sd / np.sqrt(n))
    return {"actual": actual, "expected": expected, "pct": (actual / expected - 1) * 100, "z": float(z), "weeks": n}


def severity(pct, z):
    if abs(pct) < 2:
        return "none"
    az = abs(z)
    return "high" if az >= 2 else "medium" if az >= 1.5 else "low" if az >= 1 else "none"


def detect_anomaly(request):
    con = get_con()
    metric = request["metric"]
    if metric not in ADDITIVE + ("aov",):
        raise ToolError("NO_DATA", "detect_anomaly supports net_revenue, gross_revenue, orders and aov; "
                                   "active_customers is covered by driver_decomposition")
    compare_to = request.get("compare_to", "same_period_last_year")
    p = prepare_period(con, request["period"], compare_to)
    s, e, L, ds, lf = p["s"], p["e"], p["L"], p["ds"], p["lf"]
    daily = daily_frame(con, request.get("filters"))
    last_year_ok = (s - 364 * DAY) >= ds

    yoy = yoy_check(daily, s, e, L, metric, ds, lf) if (p["last_year"] or last_year_ok) else None
    try:
        stl = stl_check(daily, s, e, metric, ds, lf)
    except Exception:
        stl = None

    if p["last_year"] and yoy is not None:
        method, prim = "yoy", yoy
        ta, tb = yoy["ta"], yoy["tb"]
    elif stl is not None:
        method, prim = "stl", stl
        ta = int(daily.loc[s:e, "trading_day"].sum())
        tb = int(daily.loc[s - 364 * DAY:e - 364 * DAY, "trading_day"].sum()) if last_year_ok else ta
    else:
        raise ToolError("NO_DATA", "could not form a baseline (STL needs a window of at least one full "
                                   "Monday-Sunday week; yoy needs data a year earlier)")

    checks = []
    for name, r in (("yoy", yoy), ("stl", stl)):
        if r is not None:
            checks.append({"method": name, "change_pct": round(r["pct"], 2), "z_score": round(r["z"], 2)})

    base_s, base_e = (s - 364 * DAY, e - 364 * DAY) if last_year_ok else (p["bs"], p["be"])
    pct = prim["pct"]
    out = {
        "metric": metric,
        "actual": round(prim["actual"], 2), "expected": round(prim["expected"], 2),
        "change_pct": round(pct, 2), "z_score": round(prim["z"], 2),
        "severity": severity(pct, prim["z"]),
        "direction": "decline" if pct < -2 else "increase" if pct > 2 else "flat",
        "method": method,
        "baseline": {"start": d(base_s), "end": d(base_e)},
        "trading_days": {"actual": int(ta), "expected": int(tb)},
        "trading_day_adjusted": bool(method == "yoy" and prim.get("adjusted", False)),
        "partial_period_excluded": p["partial"],
        "period_used": {"start": d(s), "end": d(e)},
        "checks": checks,
        "definition": DEFINITIONS[metric] + ("; expected scaled by trading days" if method == "yoy" else
                                              "; expected = STL trend+seasonal fit"),
        "data_through": d(p["dt"]),
    }
    if yoy is not None:
        out["normal_variation"] = {"windows": yoy["hist_n"], "mean_yoy_pct": round(yoy["hist_mean"], 2),
                                   "sd_yoy_pct": round(yoy["hist_sd"], 2)}
    if compare_to == "previous_period":
        out["note"] = "previous_period is not used as the expectation because of seasonality"
    return out


if __name__ == "__main__":
    req = {"metric": "net_revenue", "period": {"start": "2011-09-26", "end": "2011-11-20"},
           "compare_to": "same_period_last_year"}
    try:
        print(json.dumps(detect_anomaly(req), indent=2))
    except ToolError as err:
        print(json.dumps(err.payload(), indent=2))