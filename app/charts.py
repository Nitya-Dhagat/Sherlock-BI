"""
WhyBot — app/charts.py

Every function takes a NORMALISED dict from adapters.py and returns a Plotly
figure. No function here opens a database, reads a file, or computes a business
number: if a chart needs a value, a tool must have returned it.

Projector rules (playbook section 7): large fonts, high contrast, value labels
on every bar so the number is readable from the back of the room, no gridline
clutter, no legend where colour already says it.
"""

from __future__ import annotations

import plotly.graph_objects as go

from adapters import money, pretty_label, share_pct

# ---- palette ---------------------------------------------------------------
INK = "#17181C"
MUTED = "#6B7280"
GRID = "#E7E9EE"
DOWN = "#C8102E"      # decline
UP = "#0B7A4B"        # growth
NEUTRAL = "#334155"   # totals / baselines
ACCENT = "#1D4ED8"    # actual line
BAND = "rgba(29,78,216,0.10)"
WARN = "#B45309"

FONT = "Inter, Segoe UI, Helvetica, Arial, sans-serif"
BASE_FONT = 17
TITLE_FONT = 21


def _shell(fig, title, height=400, legend=False):
    fig.update_layout(
        title=dict(text=title, font=dict(size=TITLE_FONT, color=INK), x=0, xanchor="left"),
        font=dict(size=BASE_FONT, color=INK, family=FONT),
        plot_bgcolor="white", paper_bgcolor="white",
        height=height, margin=dict(l=10, r=20, t=58, b=40),
        hoverlabel=dict(font_size=BASE_FONT - 2, font_family=FONT),
        showlegend=legend,
        # legend sits BELOW the plot: at the top it collides with the title on a
        # 2-line projector title
        legend=dict(orientation="h", y=-0.16, yanchor="top", x=0,
                    font=dict(size=BASE_FONT - 3)),
        dragmode=False,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID,
                     tickfont=dict(size=BASE_FONT - 3))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=MUTED,
                     zerolinewidth=1, tickfont=dict(size=BASE_FONT - 3))
    return fig


def _empty(title, message):
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False, xref="paper", yref="paper",
                       x=0.5, y=0.5, font=dict(size=BASE_FONT - 1, color=MUTED),
                       align="center")
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _shell(fig, title, height=300)


# --- 1. time series with expected band --------------------------------------
def timeseries_expected(n: dict):
    """Needs detect_anomaly to return `series`. Falls back to a two-bar
    actual-vs-expected comparison when it doesn't, so the tab is never blank."""
    if not n:
        return _empty("Is the drop real?", "waiting for detect_anomaly")

    if not n["series"]:
        if n["actual"] is None or n["expected"] is None:
            return _empty("Is the drop real?", "detect_anomaly returned no series")
        fig = go.Figure(go.Bar(
            x=["Comparison period", "This period"], y=[n["expected"], n["actual"]],
            marker_color=[NEUTRAL, DOWN if (n["change_pct"] or 0) < 0 else UP],
            text=[money(n["expected"]), money(n["actual"])],
            textposition="outside", textfont=dict(size=BASE_FONT), cliponaxis=False,
        ))
        cp = n["change_pct"]
        sub = (f"  ·  {'−' if cp < 0 else '+'}{abs(cp):.1f}%"
               if cp is not None else "")
        return _shell(fig, f"Actual vs expected{sub}", height=380)

    s = n["series"]
    x = [p["period_start"] for p in s]
    fig = go.Figure()
    if all(p["lower"] is not None and p["upper"] is not None for p in s):
        fig.add_trace(go.Scatter(x=x, y=[p["upper"] for p in s], mode="lines",
                                 line=dict(width=0), hoverinfo="skip",
                                 showlegend=False))
        fig.add_trace(go.Scatter(x=x, y=[p["lower"] for p in s], mode="lines",
                                 line=dict(width=0), fill="tonexty", fillcolor=BAND,
                                 name="expected range", hoverinfo="skip"))
    if any(p["expected"] is not None for p in s):
        fig.add_trace(go.Scatter(x=x, y=[p["expected"] for p in s], mode="lines",
                                 name="expected",
                                 line=dict(color=MUTED, width=2, dash="dot")))
    fig.add_trace(go.Scatter(x=x, y=[p["actual"] for p in s], mode="lines",
                             name="actual", line=dict(color=ACCENT, width=3)))

    p = n["period"] or {}
    if p.get("start") and p.get("end"):
        fig.add_vrect(x0=p["start"], x1=p["end"], fillcolor=DOWN, opacity=0.07,
                      line_width=0, annotation_text="investigated",
                      annotation_position="top left",
                      annotation_font=dict(size=BASE_FONT - 4, color=DOWN))
    title = "Weekly net revenue vs expectation"
    cp = n["change_pct"]
    if cp is not None:
        title += f"  ·  {'−' if cp < 0 else '+'}{abs(cp):.1f}% vs {n['method']}"
    return _shell(fig, title, height=430, legend=True)


# --- 2. revenue bridge — the signature visual -------------------------------
def revenue_bridge(n: dict, anomaly: dict | None = None):
    if not n or not n["effects"]:
        return _empty("What moved revenue", "waiting for driver_decomposition")

    base = n["baseline_total"]
    cur = n["current_total"]
    if base is None and anomaly:
        base, cur = anomaly.get("expected"), anomaly.get("actual")

    labels, values, measures = [], [], []
    if base is not None:
        labels.append("Comparison<br>period"); values.append(base)
        measures.append("absolute")
    for e in n["effects"]:
        labels.append(e["name"].replace(" ", "<br>", 1))
        values.append(e["change"]); measures.append("relative")
    if cur is not None:
        labels.append("This<br>period"); values.append(cur); measures.append("total")

    fig = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        text=[money(v) for v in values], textposition="outside",
        textfont=dict(size=BASE_FONT - 1),
        connector=dict(line=dict(color=GRID, width=1)),
        decreasing=dict(marker=dict(color=DOWN)),
        increasing=dict(marker=dict(color=UP)),
        totals=dict(marker=dict(color=NEUTRAL)),
    ))
    return _shell(fig, "What moved revenue, bar by bar", height=430)


# --- 3. contributor bars (drill_down) ---------------------------------------
def contributor_bars(n: dict, dimension: str, top_k: int = 6):
    rows = (n or {}).get("contributors", {}).get(dimension, [])
    if not rows:
        return _empty("Where did it change?", "waiting for drill_down")
    rows = sorted(rows, key=lambda r: r["change"])[:top_k]
    x = [r["change"] for r in rows]
    text = []
    for r in rows:
        t = money(r["change"])
        if r["share_net"] is not None and n.get("share_meaningful", True):
            t += f"   {share_pct(r['share_net'])} of net change"
        text.append(t)
    fig = go.Figure(go.Bar(
        x=x, y=[str(r["value"]) for r in rows], orientation="h",
        marker_color=[DOWN if v < 0 else UP for v in x],
        text=text, textposition="outside", textfont=dict(size=BASE_FONT - 2),
        cliponaxis=False,
    ))
    fig.update_xaxes(title=dict(text="change vs comparison period",
                                font=dict(size=BASE_FONT - 3)))
    fig.update_yaxes(autorange="reversed")
    return _shell(fig, f"Where it changed — by {pretty_label(dimension)}",
                  height=140 + 56 * max(len(rows), 2))


# --- 4. driver bars (shares view of the same evidence) ----------------------
def driver_bars(n: dict):
    if not n or not n["effects"]:
        return _empty("How did it change?", "waiting for driver_decomposition")
    eff = n["effects"]
    has_share = all(e["share"] is not None for e in eff) and n.get("share_meaningful", True)
    y = [(e["share"] * 100 if has_share else e["change"]) for e in eff]
    fig = go.Figure(go.Bar(
        x=[e["name"] for e in eff], y=y,
        marker_color=[DOWN if e["change"] < 0 else UP for e in eff],
        text=[(f"{share_pct(e['share'])}<br>{money(e['change'])}" if has_share
               else money(e["change"])) for e in eff],
        textposition="outside", textfont=dict(size=BASE_FONT - 2), cliponaxis=False,
    ))
    fig.update_yaxes(title=dict(text="share of total change" if has_share
                                else "change", font=dict(size=BASE_FONT - 3)))
    return _shell(fig, "How it changed — behavioural drivers", height=400)


# --- 5. customer movement bridge --------------------------------------------
def customer_movement_bridge(n: dict):
    if not n:
        return _empty("Who changed?", "waiting for customer_movement")
    b = n["buckets"]
    if all(b[k]["revenue_change"] is None for k in b):
        return _empty("Who changed?", "customer_movement returned no revenue figures")

    labels, values, measures = [], [], []
    for k in ("lost", "retained", "new", "unattributed"):
        blk = b[k]
        if blk["revenue_change"] is None:
            continue
        cnt = blk["customers"]
        sub = (f"<br><span style='font-size:13px'>{cnt} customers</span>"
               if cnt is not None else
               "<br><span style='font-size:13px'>no customer id</span>")
        labels.append(f"{k.title()}{sub}")
        values.append(blk["revenue_change"])
        measures.append("relative")
    labels.append("Net<br>change")
    values.append(n["total_change"] if n["total_change"] is not None else sum(values))
    measures.append("total")

    fig = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        text=[money(v) for v in values], textposition="outside",
        textfont=dict(size=BASE_FONT - 1),
        connector=dict(line=dict(color=GRID, width=1)),
        decreasing=dict(marker=dict(color=DOWN)),
        increasing=dict(marker=dict(color=UP)),
        totals=dict(marker=dict(color=NEUTRAL)),
    ))
    conc = n["top10_concentration"]
    sub = f"  ·  top 10 accounts = {share_pct(conc)} of the effect" if conc else ""
    return _shell(fig, f"Who changed{sub}", height=420)


# --- 6. confidence components ------------------------------------------------
def confidence_components(n: dict):
    comps = (n or {}).get("components") or {}
    if not comps:
        return _empty("Confidence", "waiting for compute_confidence")
    weights = n.get("weights") or {}
    names = list(comps.keys())
    vals = [comps[k] * 100 for k in names]
    labels = []
    for k in names:
        w = weights.get(k)
        labels.append(f"{k.title()}<br><span style='font-size:12px'>"
                      f"weight {w:.2f}</span>" if w else k.title())
    fig = go.Figure(go.Bar(
        x=labels, y=vals,
        marker_color=[UP if v >= 70 else (WARN if v >= 45 else DOWN) for v in vals],
        text=[f"{v:.0f}" for v in vals], textposition="outside",
        textfont=dict(size=BASE_FONT - 1), cliponaxis=False,
    ))
    fig.update_yaxes(range=[0, 112], title=dict(text="score", font=dict(size=BASE_FONT - 3)))
    return _shell(fig, "Confidence components — computed, not claimed", height=330)


# --- 7. forecast -------------------------------------------------------------
def forecast_chart(n: dict):
    if not n or not (n["history"] or n["baseline"]):
        return _empty("What happens next?", "waiting for forecast_scenario")
    fig = go.Figure()
    if n["history"]:
        fig.add_trace(go.Scatter(x=[p["period_start"] for p in n["history"]],
                                 y=[p["value"] for p in n["history"]],
                                 mode="lines", name="actual",
                                 line=dict(color=INK, width=3)))
    if n["baseline"]:
        fig.add_trace(go.Scatter(x=[p["period_start"] for p in n["baseline"]],
                                 y=[p["value"] for p in n["baseline"]],
                                 mode="lines", name="if nothing changes",
                                 line=dict(color=MUTED, width=3, dash="dash")))
    if n["what_if"]:
        fig.add_trace(go.Scatter(x=[p["period_start"] for p in n["what_if"]],
                                 y=[p["value"] for p in n["what_if"]],
                                 mode="lines", name="if the segment recovers",
                                 line=dict(color=UP, width=3, dash="dot")))
    h = n["horizon_weeks"]
    return _shell(fig, f"Projection — {h} weeks ahead" if h else "Projection",
                  height=430, legend=True)


# --- 8. external factors ------------------------------------------------------
def external_factors_bars(n: dict):
    """One bar per external explanation that was actually tested, so a judge can
    see at a glance that the alternatives were checked and ruled out."""
    if not n:
        return _empty("External explanations", "waiting for check_external_factors")
    labels, vals, colors, texts = [], [], [], []

    td = n["trading_days"]
    if td["delta"] is not None:
        labels.append("Trading days")
        vals.append(abs(td["delta"]) * 10)
        colors.append(WARN if td["strength"] != "none" else MUTED)
        texts.append(f"{td['delta']:+d} days")
    if n["price"] and n["price"]["change_pct"] is not None:
        labels.append("Price level")
        vals.append(abs(n["price"]["change_pct"]) * 3)
        colors.append(WARN if n["price"]["strength"] != "none" else MUTED)
        texts.append(f"{n['price']['change_pct']:+.1f}%")
    av = n.get("availability")
    if av and av.get("dropped") is not None:
        labels.append("Products dropped")
        vals.append(min(abs(av["dropped"]), 100))
        colors.append(MUTED)
        texts.append(f"{av['dropped']} dropped, {av.get('launched', '—')} launched")
    for s in n["tier2_tested"]:
        if s["correlation"] is None:
            continue
        labels.append(pretty_label(str(s["series"]).replace(".csv", "")))
        vals.append(abs(s["correlation"]) * 100)
        colors.append(WARN if s["strength"] != "none" else MUTED)
        texts.append(f"effect {s['correlation']:+.2f} · {s['strength']}")

    if not labels:
        return _empty("External explanations", "no external series were testable")

    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h", marker_color=colors,
        text=texts, textposition="outside", textfont=dict(size=BASE_FONT - 2),
        cliponaxis=False,
    ))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(autorange="reversed")
    return _shell(fig, "External explanations checked (amber = may matter)",
                  height=130 + 52 * max(len(labels), 2))