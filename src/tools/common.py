"""
Shared helpers for the analytic tools (src/tools/common.py).

Database: env var WHYBOT_DB (default data/clean/whybot.duckdb).
  Real data : $env:WHYBOT_DB = "data/clean/whybot.duckdb"
  Demo data : $env:WHYBOT_DB = "data/scenario/whybot_scenario.duckdb"

Conventions (same as the README metric definitions):
  * product lines only; net = incl. cancellations (negative); gross = excl. cancellations
  * orders = distinct non-cancel invoices; active_customers = distinct non-null customer_id on non-cancel lines
  * "same period last year" = shifted by 364 days (52 weeks, same weekdays)
  * the incomplete final week is never analysed: period ends are clamped to the last full week
  * customer segments (customer_type, tenure, rfm_tier) are fixed AS OF THE CURRENT PERIOD START for both windows,
    so slices add up exactly
"""
import os
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from build_clean import customer_features_asof  # noqa: E402

DEFAULT_DB = ROOT / "data" / "clean" / "whybot.duckdb"
ADDITIVE = ("net_revenue", "gross_revenue", "orders")
LINE_DIMS = ("country", "uk_vs_international")
CUSTOMER_DIMS = ("customer_type", "tenure", "rfm_tier")
DAY = pd.Timedelta(days=1)

DEFINITIONS = {
    "net_revenue": "net_revenue = sum(quantity*unit_price) over product lines, cancellations negative, unattributed rows included",
    "gross_revenue": "gross_revenue = sum(quantity*unit_price) over product lines excluding cancellation lines",
    "orders": "orders = distinct non-cancellation invoices on product lines",
    "active_customers": "active_customers = distinct customer_id with at least one non-cancellation product line",
    "aov": "aov = gross_revenue / orders",
}


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message

    def payload(self):
        return {"error": {"code": self.code, "message": self.message}}


_CONS = {}


def get_con():
    path = Path(os.environ.get("WHYBOT_DB", str(DEFAULT_DB)))
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise ToolError("INTERNAL", f"database not found: {path}")
    key = str(path)
    if key not in _CONS:
        _CONS[key] = duckdb.connect(key, read_only=True)
    return _CONS[key]


def bounds(con):
    """(first date, last date with data, last day of the last COMPLETE week)."""
    lo, hi = con.execute("SELECT min(sale_date), max(sale_date) FROM daily_metrics").fetchone()
    wk = con.execute("SELECT max(week_start) FROM weekly_metrics WHERE NOT is_partial_week").fetchone()[0]
    return pd.Timestamp(lo), pd.Timestamp(hi), pd.Timestamp(wk) + 6 * DAY


def d(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def prepare_period(con, period, compare_to):
    """Validate the period, clamp the incomplete final week, resolve the baseline window."""
    ds, dt, lf = bounds(con)
    try:
        s, e = pd.Timestamp(period["start"]), pd.Timestamp(period["end"])
    except Exception:
        raise ToolError("BAD_PERIOD", "period needs start and end as YYYY-MM-DD")
    if s > e:
        raise ToolError("BAD_PERIOD", "period start is after period end")
    partial = False
    if e > lf:
        e, partial = lf, True
    if s > e:
        raise ToolError("BAD_PERIOD", f"period lies in the incomplete final week; last complete day is {d(lf)}")
    if s < ds:
        raise ToolError("BAD_PERIOD", f"data starts {d(ds)}")
    length = (e - s).days + 1
    if compare_to == "same_period_last_year":
        bs, be = s - 364 * DAY, e - 364 * DAY
    elif compare_to == "previous_period":
        bs, be = s - length * DAY, s - DAY
    elif isinstance(compare_to, dict):
        bs, be = pd.Timestamp(compare_to["start"]), pd.Timestamp(compare_to["end"])
    else:
        raise ToolError("BAD_PERIOD", "compare_to must be previous_period, same_period_last_year or a period")
    if bs < ds:
        raise ToolError("BAD_PERIOD", f"baseline window starts {d(bs)}, before the data begins ({d(ds)})")
    return {"s": s, "e": e, "bs": bs, "be": be, "L": length, "partial": partial,
            "last_year": (bs == s - 364 * DAY and be == e - 364 * DAY),
            "ds": ds, "dt": dt, "lf": lf}


def load_lines(con, start, end):
    df = con.execute(
        "SELECT sale_date, invoice, customer_id, is_cancel, line_revenue, country, is_uk FROM product_lines "
        f"WHERE sale_date BETWEEN DATE '{d(start)}' AND DATE '{d(end)}'").df()
    df["sale_date"] = pd.to_datetime(df["sale_date"])
    df["customer_id"] = pd.to_numeric(df["customer_id"]).astype("float64")
    df["uk_vs_international"] = np.where(df["is_uk"], "United Kingdom", "International")
    return df


def add_segments(lines, con, as_of):
    """Attach customer_type / rfm_tier / tenure, fixed as of `as_of` (no look-ahead)."""
    feats = customer_features_asof(con, as_of)
    if feats.empty:
        keep = pd.DataFrame({"customer_id": [], "customer_type": [], "rfm_tier": [], "tenure": []})
    else:
        keep = feats[["customer_id", "customer_type", "rfm_tier"]].copy()
        keep["tenure"] = "returning"
    keep["customer_id"] = keep["customer_id"].astype("float64")
    lines = lines.merge(keep, on="customer_id", how="left")
    attributed = lines["customer_id"].notna()
    for col, label in (("customer_type", "new_in_period"), ("rfm_tier", "new_in_period"), ("tenure", "new")):
        lines[col] = np.where(~attributed, "unattributed", lines[col].fillna(label))
    return lines


def apply_filters(lines, filters, allow_customer=True):
    for f in filters or []:
        dim, val = f["dimension"], f["value"]
        if dim == "category":
            raise ToolError("NO_DATA", "product category classification is not available yet")
        if dim in CUSTOMER_DIMS and not allow_customer:
            raise ToolError("NO_DATA", f"filter on {dim} is not supported here; use drill_down or significance_test")
        if dim not in LINE_DIMS + CUSTOMER_DIMS:
            raise ToolError("NO_DATA", f"unknown filter dimension: {dim}")
        lines = lines[lines[dim] == val]
    return lines


def uses_customer_dims(dims, filters):
    names = list(dims or []) + [f["dimension"] for f in (filters or [])]
    return any(n in CUSTOMER_DIMS for n in names)


def metric_value(df, metric):
    g = df[~df["is_cancel"]]
    if metric == "net_revenue":
        return float(df["line_revenue"].sum())
    if metric == "gross_revenue":
        return float(g["line_revenue"].sum())
    if metric == "orders":
        return float(g["invoice"].nunique())
    if metric == "active_customers":
        return float(g["customer_id"].dropna().nunique())
    if metric == "aov":
        n = g["invoice"].nunique()
        return float(g["line_revenue"].sum() / n) if n else float("nan")
    raise ToolError("BAD_PERIOD", f"unknown metric {metric}")


def metric_by(df, col, metric):
    g = df[~df["is_cancel"]]
    if metric == "net_revenue":
        return df.groupby(col)["line_revenue"].sum()
    if metric == "gross_revenue":
        return g.groupby(col)["line_revenue"].sum()
    if metric == "orders":
        return g.groupby(col)["invoice"].nunique().astype(float)
    raise ToolError("NO_DATA", f"{metric} is not additive across slices")


def daily_frame(con, filters=None):
    """Complete daily calendar: net_revenue, gross_revenue, orders, trading_day (optionally filtered)."""
    if not filters:
        df = con.execute("SELECT sale_date, net_revenue, gross_revenue, orders, trading_day "
                         "FROM daily_metrics ORDER BY sale_date").df()
        df["sale_date"] = pd.to_datetime(df["sale_date"])
        return df.set_index("sale_date")
    ds, dt, _ = bounds(con)
    lines = apply_filters(load_lines(con, ds, dt), filters, allow_customer=False)
    if lines.empty:
        raise ToolError("EMPTY_SLICE", "no product lines match the filters")
    gl = lines[~lines["is_cancel"]]
    df = pd.DataFrame({"net_revenue": lines.groupby("sale_date")["line_revenue"].sum(),
                       "gross_revenue": gl.groupby("sale_date")["line_revenue"].sum(),
                       "orders": gl.groupby("sale_date")["invoice"].nunique()})
    df = df.reindex(pd.date_range(ds, dt, freq="D")).fillna(0.0)
    df["trading_day"] = (df["gross_revenue"] > 0).astype(int)
    df.index.name = "sale_date"
    return df