"""
WhyBot — app/adapters.py

THE SINGLE PLACE WHERE TOOL-OUTPUT SHAPES ARE ASSUMED.

Why this file exists
--------------------
On Oct 28 morning, detect_anomaly / drill_down / driver_decomposition /
customer_movement do not exist yet. The UI is built against the contract in
playbook section 4, but the real tools will land today and some field will be
spelled differently — `effects` vs `drivers`, `revenue_change` vs `revenue`.

Without this layer, every such mismatch is a bug inside a chart function, found
on stage. With it, every mismatch is one line here, and the charts never change.

Rules:
  * Every normaliser is TOLERANT on input (tries several key spellings) and
    STRICT on output (one documented shape the charts rely on).
  * Nothing here computes a business number. Shares are passed through, not
    derived — if a tool didn't return a share, the chart shows no share.
    Playbook: "Any number on screen must exist in the evidence store."
  * Nothing here raises. A missing field becomes None, and the chart renders an
    honest empty state. The demo never dies on a KeyError.

When a real tool lands and a chart looks wrong, fix the key list in the matching
normaliser and nothing else.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------


def first(d: Any, *keys, default=None):
    """First present, non-None value among `keys` in dict `d`."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def as_float(v, default=None):
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def as_int(v, default=None):
    f = as_float(v)
    return default if f is None else int(f)


def is_error(result: Any) -> bool:
    return isinstance(result, dict) and "error" in result


def error_of(result: Any) -> dict:
    e = result.get("error") if isinstance(result, dict) else None
    if isinstance(e, dict):
        return {"code": e.get("code", "ERROR"), "message": e.get("message", "")}
    return {"code": "ERROR", "message": str(e) if e else ""}


# contract driver keys -> readable names for the chart axis
DRIVER_LABELS = {
    "active_customers": "active customers",
    "orders_per_customer": "orders per customer",
    "aov": "average order value",
    "unattributed": "unattributed revenue",
}


def pretty_label(s: Any) -> str:
    """'customer_type' -> 'customer type'; 'wholesale_like' -> 'wholesale like'."""
    return str(s).replace("_", " ").strip()


def money(v, currency="£") -> str:
    """Sign goes BEFORE the currency symbol: −£128.9k, not £-128.9k.
    Uses a true minus sign (U+2212) so it is legible on a projector."""
    f = as_float(v)
    if f is None:
        return "—"
    sign = "−" if f < 0 else ""
    a = abs(f)
    if a >= 1_000_000:
        return f"{sign}{currency}{a/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{sign}{currency}{a/1_000:.1f}k"
    return f"{sign}{currency}{a:,.0f}"


def pct(v, digits=1) -> str:
    f = as_float(v)
    if f is None:
        return "—"
    return f"{'−' if f < 0 else '+'}{abs(f):.{digits}f}%"


def share_pct(v, digits=0) -> str:
    """Shares arrive as fractions (0.71). Render as 71%."""
    f = as_float(v)
    return "—" if f is None else f"{f*100:.{digits}f}%"


# --------------------------------------------------------------------------
# per-tool normalisers
# --------------------------------------------------------------------------


def norm_anomaly(r: dict) -> dict:
    """
    Contract: actual, expected, change_pct, z_score, severity, method,
    trading_days {actual, expected}, trading_day_adjusted.
    Optional (Mugdha's ask to Nisaj): series [{period_start, actual, expected,
    lower, upper}] for the time-series chart.
    """
    td = first(r, "trading_days", default={}) or {}
    series = []
    raw_series = first(r, "series", "weekly", "history", default=[]) or []
    for p in raw_series:
        if not isinstance(p, dict):
            continue
        series.append({
            "period_start": first(p, "period_start", "week", "week_start", "date"),
            "actual": as_float(first(p, "actual", "value", "net_revenue")),
            "expected": as_float(first(p, "expected", "baseline", "fitted")),
            "lower": as_float(first(p, "lower", "lo", "ci_low")),
            "upper": as_float(first(p, "upper", "hi", "ci_high")),
        })
    return {
        "actual": as_float(first(r, "actual", "current", "current_total")),
        "expected": as_float(first(r, "expected", "baseline", "baseline_total")),
        "change_pct": as_float(first(r, "change_pct", "pct_change", "yoy_pct")),
        "change_abs": as_float(first(r, "change", "total_change", "change_abs")),
        "z_score": as_float(first(r, "z_score", "z", "zscore")),
        "severity": first(r, "severity"),
        "method": first(r, "method", default="baseline"),
        "period": first(r, "period", default={}) or {},
        "compare_to": first(r, "compare_to"),
        "baseline": first(r, "baseline", "baseline_window", default={}) or {},
        "partial_period_excluded": first(r, "partial_period_excluded"),
        "trading_days_actual": as_int(first(td, "actual", "current")),
        "trading_days_expected": as_int(first(td, "expected", "base", "baseline")),
        "trading_day_adjusted": bool(first(r, "trading_day_adjusted", default=False)),
        "series": series,
        "definition": first(r, "definition"),
        "data_through": first(r, "data_through"),
    }


MATERIAL_SHARE = 0.10   # shares of the net change are shown only if |net| >= 10% of the parts' total movement


def share_meaningful(total, changes) -> bool:
    """False when the net change is tiny next to its parts (offsetting moves): % of net would explode."""
    gross = sum(abs(c) for c in changes if c is not None)
    return bool(total) and gross > 0 and abs(total) >= MATERIAL_SHARE * gross


def top_contributor(items, total):
    """Biggest real value in the direction of the net change; the '(other)' bucket is never 'top'."""
    real = [r for r in items if str(r["value"]) != "(other)"] or items
    return max(real, key=lambda r: r["change"]) if (total or 0) > 0 else min(real, key=lambda r: r["change"])


def norm_drill(r: dict) -> dict:
    """
    Contract: total_change, contributors {dimension: [{value, change, pct_change,
    share_of_net_change, share_of_gross_decline}]}.
    """
    raw = first(r, "contributors", "by_dimension", "dimensions", default={}) or {}
    out: dict[str, list] = {}
    for dim, rows in raw.items():
        if not isinstance(rows, list):
            continue
        cleaned = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cleaned.append({
                "value": first(row, "value", "name", "label", "key"),
                "change": as_float(first(row, "change", "delta", "contribution",
                                         "change_abs")),
                "pct_change": as_float(first(row, "pct_change", "change_pct")),
                "share_net": as_float(first(row, "share_of_net_change", "share_net",
                                            "share")),
                "share_gross": as_float(first(row, "share_of_gross_decline",
                                              "share_gross")),
            })
        cleaned = [c for c in cleaned if c["change"] is not None]
        if cleaned:
            out[str(dim)] = cleaned
    total = as_float(first(r, "total_change", "net_change", "change"))
    first_rows = next(iter(out.values()), [])
    return {
        "total_change": total,
        "contributors": out,
        "share_meaningful": share_meaningful(total, [c["change"] for c in first_rows]),
        "dimensions": list(out.keys()),
    }


def norm_drivers(r: dict) -> dict:
    """
    Contract (contract/schemas.json): `effects` is a DICT keyed by driver name:
        {"active_customers": {"change": -88500.0, "share": 0.69},
         "orders_per_customer": {...}, "aov": {...}, "unattributed": {...}}
    Older drafts used a list of {name, change, share}; both are accepted.
    Note the fourth effect, `unattributed` — revenue with no customer_id, about
    13.6% of this dataset. It must be drawn or the bridge will not reach the
    total.
    """
    raw = first(r, "effects", "drivers", "components", "decomposition", default={})
    effects = []
    if isinstance(raw, dict):
        for key, e in raw.items():
            if isinstance(e, dict):
                change = as_float(first(e, "change", "effect", "contribution", "delta"))
                share = as_float(first(e, "share", "share_of_total_change"))
            else:
                change, share = as_float(e), None
            if change is None:
                continue
            effects.append({"name": DRIVER_LABELS.get(str(key), pretty_label(key)),
                            "key": str(key), "change": change, "share": share})
    elif isinstance(raw, list):
        for e in raw:
            if not isinstance(e, dict):
                continue
            change = as_float(first(e, "change", "effect", "contribution", "delta"))
            if change is None:
                continue
            key = str(first(e, "name", "driver", "component", "label", default="?"))
            effects.append({"name": DRIVER_LABELS.get(key, pretty_label(key)),
                            "key": key, "change": change,
                            "share": as_float(first(e, "share",
                                                    "share_of_total_change"))})
    total = as_float(first(r, "total_change", "net_change"))
    return {
        "effects": effects,
        "total_change": total,
        "baseline_total": as_float(first(r, "baseline_total", "expected",
                                         "base_total")),
        "current_total": as_float(first(r, "current_total", "actual", "current")),
        "method": first(r, "method"),
        "sum_check": as_float(first(r, "sum_check")),
        "effects_sum": round(sum(e["change"] for e in effects), 2) if effects else None,
        "share_meaningful": share_meaningful(total, [e["change"] for e in effects]),
    }


def norm_movement(r: dict) -> dict:
    """
    Contract: retained / lost / new -> customer counts, revenue change, share of
    total change; top-10 concentration; top lost accounts.
    """
    buckets = {}
    # `unattributed` is the fourth group in the contract: revenue with no
    # customer_id. Without it the waterfall does not reach the total.
    for key in ("lost", "retained", "new", "unattributed"):
        b = first(r, key, default={}) or {}
        if not isinstance(b, dict):
            b = {}
        buckets[key] = {
            "customers": as_int(first(b, "customers", "n", "count", "n_customers")),
            "revenue_change": as_float(first(b, "revenue_change", "revenue", "change",
                                             "delta")),
            "share": as_float(first(b, "share_of_total_change", "share")),
        }
    top_lost = []
    for a in (first(r, "top_lost_accounts", "top_lost", "top_accounts", default=[]) or []):
        if not isinstance(a, dict):
            continue
        top_lost.append({
            "customer_id": first(a, "customer_id", "id", "customer"),
            "revenue_change": as_float(first(a, "revenue_change", "change", "revenue")),
            "country": first(a, "country"),
        })
    return {
        "buckets": buckets,
        "total_change": as_float(first(r, "total_change", "net_change")),
        "top10_concentration": as_float(first(r, "top10_concentration",
                                              "top_10_concentration",
                                              "concentration")),
        "window_days": as_int(first(r, "window_days", "lost_window_days")),
        "top_lost_accounts": top_lost,
    }


def norm_significance(r: dict) -> dict:
    """
    Contract: effect_size is a FRACTION (-0.152 = -15.2%), and ci_low / ci_high
    are flat fractions beside it -- not a nested confidence_interval of currency
    amounts. An older draft nested them; both are read here.

    `effect_is_fraction` tells the UI whether to render these as percentages or
    as money, so the report card cannot print "-£0.15".
    """
    eff_raw = first(r, "effect_size", "effect")
    ci = first(r, "confidence_interval", "ci", default={}) or {}

    total_change = pct_change = None
    effect_is_fraction = False
    if isinstance(eff_raw, dict):                       # older nested shape
        total_change = as_float(first(eff_raw, "total_change", "change"))
        pct_change = as_float(first(eff_raw, "pct_change"))
    else:                                               # contract shape
        f = as_float(eff_raw)
        if f is not None:
            effect_is_fraction = True
            pct_change = f * 100

    lo = as_float(first(r, "ci_low")) if "ci_low" in r else as_float(
        first(ci, "low", "lower", "lo"))
    hi = as_float(first(r, "ci_high")) if "ci_high" in r else as_float(
        first(ci, "high", "upper", "hi"))
    if effect_is_fraction:
        lo = lo * 100 if lo is not None else None
        hi = hi * 100 if hi is not None else None

    excl = first(ci, "excludes_zero")
    if excl is None and lo is not None and hi is not None:
        excl = (lo < 0 and hi < 0) or (lo > 0 and hi > 0)

    return {
        "method": first(r, "method"),
        "n_customers": as_int(first(r, "n_customers", "n")),
        "total_change": total_change,
        "pct_change": pct_change,
        "effect_is_fraction": effect_is_fraction,
        "ci_low": lo, "ci_high": hi,
        "ci_level": as_float(first(ci, "level"), default=0.95),
        "excludes_zero": excl,
        "p_value": as_float(first(r, "p_value", "p")),
        "z": as_float(first(r, "historical_z", "z_score", "z")),
        "z_windows": as_int(first(r, "z_windows_compared", "n_windows")),
        "strength": first(r, "strength", "label", default="unknown"),
        "caveat": first(r, "caveat"),
        "segment": first(r, "segment", default=None),
        "filters": first(r, "filters", default=[]) or [],
    }


def norm_external(r: dict) -> dict:
    """
    Contract shape is flat:
        trading_days {actual, expected, delta}
        price_index_change_pct, products_dropped, products_launched
        holiday_flags [], tested_series [{name, effect, strength}], no_data []
    An older draft nested these under tier1/tier2_tested; both are read.
    """
    t1 = first(r, "tier1", "tier_1", default={}) or {}
    td = first(r, "trading_days", default=None)
    if td is None:
        td = first(t1, "trading_days", default={}) or {}
    cur = as_int(first(td, "actual", "current"))
    base = as_int(first(td, "expected", "base", "baseline"))
    delta = as_int(first(td, "delta"))
    if delta is None and cur is not None and base is not None:
        delta = cur - base

    price_pct = as_float(first(r, "price_index_change_pct"))
    price_strength = None
    if price_pct is None:
        pblock = first(t1, "price", "price_index", default={}) or {}
        price_pct = as_float(first(pblock, "price_index_change_pct", "change_pct"))
        price_strength = first(pblock, "strength")

    dropped = as_int(first(r, "products_dropped"))
    launched = as_int(first(r, "products_launched"))
    dropped_rev = launched_rev = None
    if dropped is None:
        av = first(t1, "availability", default={}) or {}
        dropped = as_int(first(av, "products_dropped", "dropped"))
        launched = as_int(first(av, "products_launched", "launched"))
        dropped_rev = as_float(first(av, "revenue_at_risk_from_dropped"))
        launched_rev = as_float(first(av, "revenue_from_launched"))

    tested = []
    for sx in (first(r, "tested_series", "tier2_tested", "tested", default=[]) or []):
        if not isinstance(sx, dict):
            continue
        tested.append({
            "series": first(sx, "name", "series"),
            "status": first(sx, "status", default="tested"),
            "basis": first(sx, "basis"),
            # the contract calls it `effect`; an older draft called it correlation
            "correlation": as_float(first(sx, "effect", "correlation", "corr",
                                          "rho")),
            "n_weeks": as_int(first(sx, "n_weeks", "n")),
            "delta": first(sx, "delta"),
            "strength": first(sx, "strength", default="none"),
        })

    holidays = []
    for h in (first(r, "holiday_flags", default=[]) or []):
        holidays.append(h if isinstance(h, str)
                        else first(h, "name", "holiday", default=str(h)))

    no_data = []
    for n in (first(r, "no_data", "not_checked", default=[]) or []):
        if isinstance(n, dict):
            no_data.append({"series": first(n, "series", "name"),
                            "reason": first(n, "reason", default="")})
        else:
            no_data.append({"series": str(n), "reason": ""})

    return {
        "trading_days": {"current": cur, "base": base, "delta": delta,
                         "strength": first(td, "strength",
                                           default=("supporting"
                                                    if delta is not None
                                                    and abs(delta) >= 2
                                                    else "none"))},
        "price": {"change_pct": price_pct, "basket": None,
                  "strength": price_strength or ("supporting"
                                                 if price_pct is not None
                                                 and abs(price_pct) >= 5
                                                 else "none")}
        if price_pct is not None else None,
        "availability": {"dropped": dropped, "dropped_revenue": dropped_rev,
                         "launched": launched, "launched_revenue": launched_rev}
        if dropped is not None else None,
        "holidays": holidays,
        "tier2_tested": tested,
        "no_data": no_data,
        "caveat": first(r, "caveat"),
    }


def _series_points(raw) -> list:
    out = []
    for p in (raw or []):
        if not isinstance(p, dict):
            continue
        x = first(p, "period_start", "week", "week_start", "date")
        y = as_float(first(p, "value", "actual", "net_revenue"))
        if x is not None and y is not None:
            out.append({"period_start": x, "value": y})
    return out


def norm_forecast(r: dict) -> dict:
    """Contract: baseline projection and what-if, horizon, method, caveat text."""
    return {
        # the contract returns no history series, so the chart draws the
        # projection alone; _series_points already accepts week_start
        "history": _series_points(first(r, "history", "actual", default=[])),
        "baseline": _series_points(first(r, "baseline", "projection", default=[])),
        "what_if": _series_points(first(r, "what_if", "whatif", "scenario", default=[])),
        "horizon_weeks": as_int(first(r, "horizon_weeks", "horizon")),
        "method": first(r, "method"),
        "cumulative_gap": as_float(first(r, "cumulative_gap", "gap")),
        "gap_note": first(r, "cumulative_gap_note", "gap_note"),
        "caveat": first(r, "caveat"),
        "segment": first(r, "recover_segment", "segment", default=[]) or [],
    }


def norm_report(rep: dict) -> dict:
    """submit_report payload: headline, findings (each citing evidence ids),
    actions, hypotheses."""
    rep = rep or {}
    findings = []
    for f in (first(rep, "findings", "claims", default=[]) or []):
        if isinstance(f, dict):
            findings.append({
                "text": first(f, "text", "finding", "claim", default=""),
                "evidence_ids": first(f, "evidence_ids", "evidence", default=[]) or [],
            })
        elif f:
            findings.append({"text": str(f), "evidence_ids": []})
    hypotheses = []
    for h in (first(rep, "hypotheses", "leads", default=[]) or []):
        if isinstance(h, dict):
            hypotheses.append({
                "text": first(h, "text", "hypothesis", default=""),
                "why": first(h, "why_unverifiable", "why", "reason"),
            })
        elif h:
            hypotheses.append({"text": str(h), "why": None})
    actions = [a if isinstance(a, str) else first(a, "text", "action", default=str(a))
               for a in (first(rep, "actions", "recommendations", default=[]) or [])]
    return {
        "headline": first(rep, "headline", "summary", "answer", default=""),
        "findings": findings,
        "actions": actions,
        "hypotheses": hypotheses,
        "period": first(rep, "period", default={}) or {},
        "compare_to": first(rep, "compare_to"),
        "no_answer": first(rep, "no_answer", "cannot_answer"),
    }


# playbook section 8 — weights are Nitya's to tune; the UI only displays
CONFIDENCE_ORDER = ["coverage", "significance", "specificity", "robustness"]
CONFIDENCE_MEANING = {
    "coverage": "Share of the decline explained by the identified segment",
    "significance": "Is the change distinguishable from normal variation",
    "specificity": "How concentrated the explanation is",
    "robustness": "Fraction of checks that agree",
}


def norm_confidence(c: dict) -> dict:
    c = c or {}
    comps = first(c, "components", "component_scores", default={}) or {}
    weights = first(c, "weights", default={}) or {}
    score = as_float(first(c, "score", "confidence", "value"))
    band = first(c, "band", "label")
    if band is None and score is not None:
        band = "High" if score >= 75 else "Medium" if score >= 50 else "Low"
    return {
        "score": score,
        "band": band,
        "components": {k: as_float(comps.get(k)) for k in CONFIDENCE_ORDER
                       if comps.get(k) is not None},
        "weights": {k: as_float(weights.get(k)) for k in CONFIDENCE_ORDER
                    if weights.get(k) is not None},
        "flags": first(c, "flags", default=[]) or [],
    }


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

NORMALISERS = {
    "detect_anomaly": norm_anomaly,
    "drill_down": norm_drill,
    "driver_decomposition": norm_drivers,
    "customer_movement": norm_movement,
    "significance_test": norm_significance,
    "check_external_factors": norm_external,
    "forecast_scenario": norm_forecast,
}


def normalise(tool: str, result: dict):
    """Normalise one tool result, or return None if the tool is unknown/errored."""
    if is_error(result) or not isinstance(result, dict):
        return None
    fn = NORMALISERS.get(tool)
    if fn is None:
        return None
    try:
        return fn(result)
    except Exception:
        # never break the demo over a shape surprise
        return None


# --------------------------------------------------------------------------
# step summary rows — the "small result table" the playbook asks for
# --------------------------------------------------------------------------


def step_rows(tool: str, n: dict) -> list[tuple[str, str]]:
    """Compact key/value rows shown under a trace step. Pure formatting."""
    if n is None:
        return []
    try:
        if tool == "detect_anomaly":
            rows = [("Actual", money(n["actual"])), ("Expected", money(n["expected"])),
                    ("Change", pct(n["change_pct"]))]
            if n["z_score"] is not None:
                z = n["z_score"]
                rows.append(("z-score", f"{'−' if z < 0 else ''}{abs(z):.2f}"))
            if n["trading_days_actual"] is not None:
                rows.append(("Trading days",
                             f"{n['trading_days_actual']} vs {n['trading_days_expected']}"))
            return rows
        if tool == "drill_down":
            rows = [("Total change", money(n["total_change"]))]
            for dim, items in list(n["contributors"].items())[:3]:
                top = top_contributor(items, n["total_change"])
                if n.get("share_meaningful") and top["share_net"] is not None:
                    share = f" · {share_pct(top['share_net'])} of net"
                elif top["change"] < 0 and top["share_gross"]:
                    share = f" · {share_pct(top['share_gross'])} of gross decline"
                else:
                    share = ""
                rows.append((f"Top {pretty_label(dim)}", f"{top['value']} · {money(top['change'])}{share}"))
            return rows
        if tool == "driver_decomposition":
            show = n.get("share_meaningful", True)
            rows = [(pretty_label(e["name"]),
                     f"{money(e['change'])}" + (f" · {share_pct(e['share'])}" if show else ""))
                    for e in n["effects"]]
            if n["total_change"] is not None:
                rows.append(("Total", money(n["total_change"])))
            return rows
        if tool == "customer_movement":
            rows = []
            for k in ("lost", "retained", "new", "unattributed"):
                b = n["buckets"][k]
                if b["customers"] is None and b["revenue_change"] is None:
                    continue
                who = (f"{b['customers']} customers · "
                       if b["customers"] is not None else "")
                rows.append((k.title(), f"{who}{money(b['revenue_change'])}"))
            if n["top10_concentration"] is not None:
                rows.append(("Top 10 accounts", share_pct(n["top10_concentration"])))
            return rows
        if tool == "significance_test":
            # the contract reports the effect as a percentage, not currency
            if n["effect_is_fraction"]:
                eff = pct(n["pct_change"])
                ci = f"[{pct(n['ci_low'])}, {pct(n['ci_high'])}]"
            else:
                eff = f"{money(n['total_change'])} ({pct(n['pct_change'])})"
                ci = f"[{money(n['ci_low'])}, {money(n['ci_high'])}]"
            rows = [("Strength", str(n["strength"]).title()),
                    ("Effect", eff),
                    (f"{int(n['ci_level']*100)}% interval", ci)]
            if n["p_value"] is not None:
                rows.append(("p-value", f"{n['p_value']:.4f}"))
            if n["n_customers"] is not None:
                rows.append(("Customers", f"{n['n_customers']:,}"))
            return rows
        if tool == "check_external_factors":
            td = n["trading_days"]
            d = td["delta"]
            sign = "" if d is None else ("+" if d > 0 else "−" if d < 0 else "±")
            rows = [("Trading days",
                     f"{td['current']} vs {td['base']} ({sign}{abs(d)})"
                     if d is not None else "—")]
            if n["price"]:
                rows.append(("Price index", pct(n["price"]["change_pct"])))
            if n["availability"]:
                a = n["availability"]
                txt = str(a["dropped"])
                if a["dropped_revenue"] is not None:
                    txt += f" · {money(a['dropped_revenue'])}"
                if a["launched"] is not None:
                    txt += f"  ({a['launched']} launched)"
                rows.append(("Products dropped", txt))
            for s in n["tier2_tested"][:2]:
                c = s["correlation"]
                if c is not None:
                    rows.append((str(s["series"]),
                                 f"r = {'−' if c < 0 else '+'}{abs(c):.2f}"
                                 f" · {s['strength']}"))
            if n["no_data"]:
                rows.append(("Not checkable",
                             ", ".join(str(x["series"]) for x in n["no_data"][:3])))
            return rows
        if tool == "forecast_scenario":
            rows = [("Horizon", f"{n['horizon_weeks']} weeks"
                     if n["horizon_weeks"] else "—")]
            if n["cumulative_gap"] is not None:
                rows.append(("Gap vs baseline", money(n["cumulative_gap"])))
            if n["method"]:
                rows.append(("Method", str(n["method"])))
            return rows
    except Exception:
        return []
    return []