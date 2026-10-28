"""
WhyBot — src/tools/check_external_factors.py   (v2: real schema)

Honest external checks, two tiers.

  Tier 1 (always, from product_lines itself)
    - trading days: days with non-cancel sales in each window
    - price index: Laspeyres on products sold in BOTH windows
    - availability: products that sold in the base window and vanished, and launches

  Tier 2 (optional, from small committed CSVs in data/external/)
    - UK bank holidays in each window
    - one or two series (GBP/EUR, GBP/USD), tested on WEEKLY CHANGES

Rules enforced in code:
  * Correlate changes, not levels.
  * Cap Tier 2 at MAX_TIER2 series — ~100 weekly points cannot support more.
  * Anything not checkable goes in `no_data` with a reason, never dropped silently.
  * CSV dates are snapped to the Monday of their ISO week before joining, so a
    daily or Friday-stamped file still aligns with product_lines.week_start.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta

import numpy as np

from _segments import UnknownDimension, filters_sql, lines_cte, revenue_expr

MAX_TIER2 = 3
CORR_STRONG = 0.5
CORR_WEAK = 0.3
MIN_POINTS = 20
TRADING_DAY_MATERIAL = 2
EXTERNAL_DIR = os.environ.get("WHYBOT_EXTERNAL_DIR", "data/external")


def _d(s):
    return datetime.fromisoformat(str(s)[:10]).date()


def _monday(d):
    return d - timedelta(days=d.weekday())


def _trading_days(con, as_of, start, end, filters):
    where, params = filters_sql(filters)
    sql = f"""
        WITH {lines_cte(as_of)}
        SELECT count(DISTINCT sale_date) FROM lines
        WHERE sale_date >= ? AND sale_date <= ? AND NOT is_cancel {where}
    """
    return int(con.execute(sql, [start, end, *params]).fetchone()[0])


def _price_index(con, as_of, cur, base, filters):
    """Laspeyres: base-period quantities revalued at current prices, common basket."""
    where, params = filters_sql(filters)
    sql = f"""
        WITH {lines_cte(as_of)},
        b AS (SELECT stock_code, sum(quantity) q,
                     sum(line_revenue)/nullif(sum(quantity),0) p
              FROM lines WHERE sale_date BETWEEN ? AND ? AND NOT is_cancel {where}
              GROUP BY 1),
        c AS (SELECT stock_code,
                     sum(line_revenue)/nullif(sum(quantity),0) p
              FROM lines WHERE sale_date BETWEEN ? AND ? AND NOT is_cancel {where}
              GROUP BY 1)
        SELECT sum(b.q*c.p), sum(b.q*b.p), count(*)
        FROM b JOIN c USING (stock_code) WHERE b.p > 0 AND c.p > 0 AND b.q > 0
    """
    at_new, at_old, n = con.execute(
        sql, [base["start"], base["end"], *params, cur["start"], cur["end"], *params]
    ).fetchone()
    if not at_old or not n:
        return None
    pct = (at_new / at_old - 1) * 100
    return {"price_index_change_pct": round(pct, 2),
            "products_in_common_basket": int(n),
            "strength": "supporting" if abs(pct) >= 5 else "none",
            "method": "laspeyres, base-period quantities at current prices"}


def _availability(con, as_of, cur, base, filters, top_k=10):
    where, params = filters_sql(filters)
    sql = f"""
        WITH {lines_cte(as_of)},
        b AS (SELECT stock_code, any_value(description) d, sum(line_revenue) rev
              FROM lines WHERE sale_date BETWEEN ? AND ? AND NOT is_cancel {where}
              GROUP BY 1),
        c AS (SELECT stock_code, any_value(description) d, sum(line_revenue) rev
              FROM lines WHERE sale_date BETWEEN ? AND ? AND NOT is_cancel {where}
              GROUP BY 1)
        SELECT 'dropped', b.stock_code, b.d, b.rev
          FROM b LEFT JOIN c USING (stock_code) WHERE c.stock_code IS NULL
        UNION ALL
        SELECT 'launched', c.stock_code, c.d, c.rev
          FROM c LEFT JOIN b USING (stock_code) WHERE b.stock_code IS NULL
    """
    rows = con.execute(
        sql, [base["start"], base["end"], *params, cur["start"], cur["end"], *params]
    ).fetchall()
    dropped = sorted([r for r in rows if r[0] == "dropped"], key=lambda r: -r[3])
    launched = sorted([r for r in rows if r[0] == "launched"], key=lambda r: -r[3])
    lost_rev = sum(r[3] for r in dropped)
    return {
        "products_dropped": len(dropped),
        "revenue_at_risk_from_dropped": round(lost_rev, 2),
        "top_dropped": [{"stock_code": r[1], "description": r[2],
                         "base_period_revenue": round(r[3], 2)} for r in dropped[:top_k]],
        "products_launched": len(launched),
        "revenue_from_launched": round(sum(r[3] for r in launched), 2),
        "note": "A dropped product is one with no sales in the current window. That is "
                "a proxy for availability, not a stock-out record.",
    }


def _weekly_revenue(con, as_of, filters, metric="net_revenue"):
    where, params = filters_sql(filters)
    sql = f"""
        WITH {lines_cte(as_of)}
        SELECT week_start, {revenue_expr(metric)} AS rev
        FROM lines WHERE TRUE {where} GROUP BY 1 ORDER BY 1
    """
    return {str(r[0])[:10]: float(r[1]) for r in con.execute(sql, params).fetchall()}


def _load_csv_series(name):
    """
    CSV with columns date,value. Any frequency: values are averaged into the Monday
    of their ISO week so the join against week_start cannot silently miss.
    """
    path = os.path.join(EXTERNAL_DIR, name)
    if not os.path.exists(path):
        return None, f"{EXTERNAL_DIR}/{name} not committed"
    buckets = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "date" not in reader.fieldnames \
                or "value" not in reader.fieldnames:
            return None, f"{name} needs header columns 'date,value'"
        for row in reader:
            try:
                wk = str(_monday(_d(row["date"])))
                buckets.setdefault(wk, []).append(float(row["value"]))
            except (ValueError, KeyError, TypeError):
                continue
    if not buckets:
        return None, f"{name} contained no parseable rows"
    return {k: sum(v) / len(v) for k, v in buckets.items()}, None


def _corr_on_changes(rev_weekly, series_weekly, label):
    keys = sorted(set(rev_weekly) & set(series_weekly))
    if len(keys) < MIN_POINTS:
        return {"series": label, "status": "insufficient_overlap",
                "weeks_overlapping": len(keys),
                "hint": "check the CSV's date column covers the same period"}
    dr = np.diff(np.array([rev_weekly[k] for k in keys]))
    ds = np.diff(np.array([series_weekly[k] for k in keys]))
    if dr.std() == 0 or ds.std() == 0:
        return {"series": label, "status": "no_variation"}
    rho = float(np.corrcoef(dr, ds)[0, 1])
    strength = ("supporting" if abs(rho) >= CORR_STRONG
                else "weak" if abs(rho) >= CORR_WEAK else "none")
    return {"series": label, "status": "tested", "basis": "week-over-week changes",
            "correlation": round(rho, 3), "n_weeks": int(len(dr)), "strength": strength}


def _holiday_flags(cur, base):
    try:
        import holidays
    except ImportError:
        return None, "holidays package not installed"
    uk = holidays.UnitedKingdom(
        years=range(_d(base["start"]).year, _d(cur["end"]).year + 1))

    def names(win):
        a, b = _d(win["start"]), _d(win["end"])
        return sorted(f"{d} {uk.get(d)}" for d in uk if a <= d <= b)

    c, b = names(cur), names(base)
    return {"series": "uk_bank_holidays", "status": "tested", "basis": "count in window",
            "current_period": c, "base_period": b, "delta": len(c) - len(b),
            "strength": "supporting" if len(c) != len(b) else "none"}, None


def check_external_factors(request: dict, con=None) -> dict:
    """
    request = {"period": {"start","end"}, "compare_to": {"start","end"},
               "filters": [...], "tier2": ["fx_gbp_eur.csv"]}
    """
    try:
        cur = request["period"]
        base = request["compare_to"]
        if isinstance(base, str):
            return {"error": {"code": "NEEDS_EXPLICIT_RANGE",
                              "message": "Resolve compare_to into {start,end} first."}}
        filters = request.get("filters") or []
        as_of = base["start"]
        tier2_req = (request.get("tier2") or ["fx_gbp_eur.csv"])[:MAX_TIER2]

        td_cur = _trading_days(con, as_of, cur["start"], cur["end"], filters)
        td_base = _trading_days(con, as_of, base["start"], base["end"], filters)
        td_delta = td_cur - td_base
        tier1 = {
            "trading_days": {
                "current": td_cur, "base": td_base, "delta": td_delta,
                "implied_revenue_effect_pct": round(td_delta / td_base * 100, 1)
                if td_base else None,
                "strength": "supporting" if abs(td_delta) >= TRADING_DAY_MATERIAL else "none",
            },
            "price": _price_index(con, as_of, cur, base, filters),
            "availability": _availability(con, as_of, cur, base, filters),
        }

        rev_weekly = _weekly_revenue(con, as_of, filters)
        tested, no_data = [], []
        for name in tier2_req:
            s, why = _load_csv_series(name)
            (no_data.append({"series": name, "reason": why}) if s is None
             else tested.append(_corr_on_changes(rev_weekly, s, name)))

        hol, why = _holiday_flags(cur, base)
        (tested.append(hol) if hol else
         no_data.append({"series": "uk_bank_holidays", "reason": why}))

        no_data += [
            {"series": "competitor_activity", "reason": "not in dataset scope"},
            {"series": "marketing_spend", "reason": "not in dataset scope"},
            {"series": "stock_outs",
             "reason": "no inventory table; availability above is a proxy only"},
        ]

        return {
            "period": cur, "compare_to": base, "filters": filters,
            "customer_type_as_of": as_of,
            "tier1": tier1, "tier2_tested": tested, "no_data": no_data,
            "max_series_tested": MAX_TIER2,
            "caveat": "Correlation on weekly changes is suggestive, not causal. At most "
                      f"{MAX_TIER2} external series are tested, so this is not fishing.",
            "definition": "Tier 1 from product_lines; Tier 2 from committed CSVs in "
                          "data/external (no live network calls).",
        }
    except UnknownDimension as e:
        return {"error": {"code": "UNKNOWN_DIMENSION", "message": str(e)}}
    except KeyError as e:
        return {"error": {"code": "BAD_REQUEST", "message": f"missing field {e}"}}
    except Exception as e:
        return {"error": {"code": "TOOL_FAILED", "message": str(e)[:300]}}
