"""
build_ground_truth.py - evaluation Q&A computed straight from the raw Excel file, independently of the pipeline.

Run from the repo root:   python src/eval/build_ground_truth.py
Reads   data/raw/online_retail_II.xlsx            (both sheets; pandas + calamine, NOT DuckDB / build_clean)
Writes  tests/eval/ground_truth.json              machine-readable: questions, expected facts, expected behaviour
        tests/eval/ground_truth.md                the same as a readable Q&A

Why independent: it re-implements the playbook §3.3 definitions in plain pandas, so it can grade both the agent's
decisions AND catch pipeline bugs (tests/test_ground_truth.py compares the tools against it).
Definitions: product line = stock code ^[0-9]{5}[A-Z]{0,2}$ and price > 0, exact duplicates removed, bulk
buy-and-cancel pairs (>= 10,000 units) removed; net = incl. cancellations (invoice starts with C) as negatives.
Baseline = same window 364 days earlier. The incomplete final week (after the last full Mon-Sun week) is never used.
Anomaly rule = the tool's agreed rule: yoy change adjusted by trading days, z against all other windows of the same
length (stepping 7 days, overlaps excluded); severity |z| >= 2 high, >= 1.5 medium, >= 1 low, |change| < 2% none.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXCEL = ROOT / "data" / "raw" / "online_retail_II.xlsx"
OUT_DIR = ROOT / "tests" / "eval"
COLS = {"Invoice": "invoice", "StockCode": "stock_code", "Description": "description", "Quantity": "quantity",
        "InvoiceDate": "invoice_date", "Price": "unit_price", "Customer ID": "customer_id", "Country": "country"}
CODE_RX, BULK_QTY, LOST_WINDOW = r"^[0-9]{5}[A-Z]{0,2}$", 10000, 90
DAY = pd.Timedelta(days=1)


# ---------------------------------------------------------------- load + clean (independent of build_clean.py)
def load_lines():
    sheets = pd.read_excel(EXCEL, sheet_name=None, engine="calamine")
    df = pd.concat(sheets.values(), ignore_index=True).rename(columns=COLS)[list(COLS.values())]
    raw_rows = len(df)
    for c in ("invoice", "description", "country"):
        df[c] = df[c].astype("string").str.strip()
    df["stock_code"] = df["stock_code"].astype("string").str.strip().str.upper()
    df["customer_id"] = pd.to_numeric(df["customer_id"], errors="coerce")
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df = df.drop_duplicates(keep="first")
    df["is_cancel"] = df["invoice"].str.startswith("C")
    df["is_product"] = df["stock_code"].str.fullmatch(CODE_RX).fillna(False) & (df["unit_price"] > 0)

    # bulk buy-and-cancel pairs: same customer, code, price and opposite quantity, order on/before the cancel
    p = df[df["is_product"]]
    c = p[p["is_cancel"] & (p["quantity"] <= -BULK_QTY)].reset_index()
    o = p[~p["is_cancel"]].reset_index()
    o["neg_qty"] = -o["quantity"]
    m = c.merge(o, left_on=["customer_id", "stock_code", "unit_price", "quantity"],
                right_on=["customer_id", "stock_code", "unit_price", "neg_qty"], suffixes=("_c", "_o"))
    m = m[m["invoice_date_o"] <= m["invoice_date_c"]]
    bulk = set(m["index_c"]) | set(m["index_o"])

    lines = df[df["is_product"] & ~df.index.isin(bulk)].copy()
    lines["line_revenue"] = lines["quantity"] * lines["unit_price"]
    lines["sale_date"] = lines["invoice_date"].dt.normalize()
    stats = {"raw_rows": raw_rows, "product_line_rows": len(lines), "bulk_reversal_rows": len(bulk),
             "net_revenue_total": round(float(lines["line_revenue"].sum()), 2)}
    return lines[["invoice", "customer_id", "country", "is_cancel", "line_revenue", "sale_date"]], stats


# ---------------------------------------------------------------- metric helpers
class Data:
    def __init__(self, lines):
        self.l = lines
        g = lines[~lines["is_cancel"]]
        self.ds, self.dt = lines["sale_date"].min(), lines["sale_date"].max()
        week0 = self.dt - pd.Timedelta(days=self.dt.dayofweek)          # Monday of the final week
        self.lf = week0 - DAY if self.dt.dayofweek < 6 else self.dt       # last day of the last COMPLETE week
        cal = pd.date_range(self.ds, self.dt, freq="D")
        self.daily_net = lines.groupby("sale_date")["line_revenue"].sum().reindex(cal, fill_value=0.0)
        self.trading = (g.groupby("sale_date")["line_revenue"].sum().reindex(cal, fill_value=0.0) > 0).astype(int)
        self.first_order = g.dropna(subset=["customer_id"]).groupby("customer_id")["sale_date"].min()

    def win(self, s, e):
        return self.l[(self.l["sale_date"] >= s) & (self.l["sale_date"] <= e)]

    def yoy(self, s, e):
        a, b = self.daily_net[s:e].sum(), self.daily_net[s - 364 * DAY:e - 364 * DAY].sum()
        ta, tb = int(self.trading[s:e].sum()), int(self.trading[s - 364 * DAY:e - 364 * DAY].sum())
        exp = b * ta / tb if ta and tb else b
        return float(a), float(b), ta, tb, (a / exp - 1) * 100 if exp else float("nan")

    def anomaly(self, s, e):
        L = (e - s).days + 1
        a, b, ta, tb, pct = self.yoy(s, e)
        hist = [self.yoy(st, st + (L - 1) * DAY)[4]
                for st in pd.date_range(self.ds + 364 * DAY, self.lf - (L - 1) * DAY, freq="7D")
                if not (st <= e and st + (L - 1) * DAY >= s)]
        hist = [h for h in hist if np.isfinite(h)]
        z = (pct - np.mean(hist)) / np.std(hist, ddof=1)
        sev = "none" if abs(pct) < 2 else ("high" if abs(z) >= 2 else "medium" if abs(z) >= 1.5
                                          else "low" if abs(z) >= 1 else "none")
        return {"net_revenue_current": round(a, 2), "net_revenue_baseline": round(b, 2),
                "net_change": round(a - b, 2), "raw_change_pct": round((a / b - 1) * 100, 2),
                "trading_days": {"current": ta, "baseline": tb},
                "adjusted_change_pct": round(pct, 2), "z_score": round(float(z), 2), "severity": sev,
                "is_anomaly": sev in ("medium", "high"),
                "direction": "decline" if pct < -2 else "increase" if pct > 2 else "flat"}

    def by_country(self, s, e, rising=False):
        cur, base = self.win(s, e), self.win(s - 364 * DAY, e - 364 * DAY)
        ch = (cur.groupby("country")["line_revenue"].sum()
              .sub(base.groupby("country")["line_revenue"].sum(), fill_value=0.0))
        gross = float(ch[ch < 0].sum())
        uk = float(ch.get("United Kingdom", 0.0))
        order = ch.sort_values()
        top = order.index[-1] if rising else order.index[0]          # biggest gain for a rise, else biggest fall
        return {"top_country": top, "top_country_change": round(float(ch[top]), 2),
        "top_country_share_of_gross_decline": round(float(ch[top]) / gross, 4) if ch[top] < 0 and gross else 0.0,
                "uk_change": round(uk, 2), "international_change": round(float(ch.sum()) - uk, 2)}

    def tenure(self, s, e):
        cur, base = self.win(s, e), self.win(s - 364 * DAY, e - 364 * DAY)
        returning = set(self.first_order[self.first_order < s].index)

        def split(df):
            att = df["customer_id"].notna()
            ret = att & df["customer_id"].isin(returning)
            return {"returning": df.loc[ret, "line_revenue"].sum(), "new": df.loc[att & ~ret, "line_revenue"].sum(),
                    "unattributed": df.loc[~att, "line_revenue"].sum()}
        c, b = split(cur), split(base)
        return {k: round(float(c[k] - b[k]), 2) for k in c}

    def drivers(self, s, e):
        def stats(df):
            att = df[df["customer_id"].notna()]
            g = att[~att["is_cancel"]]
            return (float(att["line_revenue"].sum()), float(df[df["customer_id"].isna()]["line_revenue"].sum()),
                    g["customer_id"].nunique(), g["invoice"].nunique())
        R0, U0, C0, O0 = stats(self.win(s - 364 * DAY, e - 364 * DAY))
        R1, U1, C1, O1 = stats(self.win(s, e))
        L = (R1 - R0) / np.log(R1 / R0) if abs(R1 - R0) > 1e-9 else R0
        eff = {"active_customers": L * np.log(C1 / C0), "orders_per_customer": L * np.log((O1 / C1) / (O0 / C0)),
               "aov": L * np.log((R1 / O1) / (R0 / O0)), "unattributed": U1 - U0}
        return {"levels": {"baseline": {"active_customers": C0, "orders": O0, "aov": round(R0 / O0, 2)},
                           "current": {"active_customers": C1, "orders": O1, "aov": round(R1 / O1, 2)}},
                "effects": {k: round(float(v), 2) for k, v in eff.items()},
                "dominant_driver": max(eff, key=lambda k: abs(eff[k]))}

    def movement(self, s, e):
        L = (e - s).days + 1
        ext = min(self.lf, s + (max(LOST_WINDOW, L) - 1) * DAY)
        P, B, X = self.win(s, e), self.win(s - 364 * DAY, e - 364 * DAY), self.win(s, ext)

        def active(df):
            return set(df[df["customer_id"].notna() & ~df["is_cancel"]]["customer_id"])
        ab, ap, ax = active(B), active(P), active(X)
        ids = set(P["customer_id"].dropna()) | set(B["customer_id"].dropna())
        lost, new = ab - ax, ap - ab
        ret = ids - lost - new
        ch = (P.groupby("customer_id")["line_revenue"].sum()
              .sub(B.groupby("customer_id")["line_revenue"].sum(), fill_value=0.0))
        grp = {n: {"customers": len(g), "revenue_change": round(float(ch.reindex(list(g)).fillna(0).sum()), 2)}
               for n, g in (("retained", ret), ("lost", lost), ("new", new))}
        grp["lost_check_window_end"] = ext.strftime("%Y-%m-%d")
        return grp


# ---------------------------------------------------------------- question selection
def candidate_windows(data, weeks):
    L = 7 * weeks
    first = data.ds + 364 * DAY + pd.Timedelta(days=(7 - (data.ds + 364 * DAY).dayofweek) % 7)   # a Monday
    out = []
    for s in pd.date_range(first, data.lf - (L - 1) * DAY, freq="7D"):
        e = s + (L - 1) * DAY
        out.append((s, e, data.yoy(s, e)[4]))
    return out


def ds_(t):
    return t.strftime("%Y-%m-%d")


def analytic_case(data, qid, s, e, why):
    an = data.anomaly(s, e)
    facts = {"period": {"start": ds_(s), "end": ds_(e)}, "compare_to": "same_period_last_year",
             "baseline": {"start": ds_(s - 364 * DAY), "end": ds_(e - 364 * DAY)},
             "anomaly": an, "country": data.by_country(s, e, an["net_change"] > 0), "tenure_change": data.tenure(s, e),
             "drivers": data.drivers(s, e), "customer_movement": data.movement(s, e)}
    if an["is_anomaly"]:
        must = ["detect_anomaly", "drill_down", "driver_decomposition", "customer_movement"]
        behaviour = (f"Confirm a real {an['direction']} ({an['severity']} severity). Locate it (top country "
                     f"{facts['country']['top_country']}), explain HOW (dominant driver: "
                     f"{facts['drivers']['dominant_driver']}) and WHO (lost vs new customers), then test the top "
                     "segment's significance. Report 'driven by'/'consistent with', not proof.")
    else:
        must = ["detect_anomaly"]
        behaviour = ("Say the change is within normal variation (severity "
                     f"{an['severity']}); do not invent a cause. Further drill-down is optional.")
    verb = {"increase": "rise", "decline": "drop"}.get(an["direction"], "change")
    q = f"Why did revenue {verb} between {ds_(s)} and {ds_(e)} compared with last year?"
    return {"id": qid, "type": "analytic", "question": q, "why_selected": why, "expected_facts": facts,
            "expected_tools_required": must, "expected_behaviour": behaviour}


BEHAVIOUR_CASES = [
    {"id": "B1", "type": "unanswerable", "question": "Did a competitor take our customers?",
     "expected_tools_required": [],
     "expected_behaviour": "Headline says the data cannot show competitor activity; may describe what the data does "
                           "show; competitor ideas only under hypotheses; data_gaps lists competitor data. Must not "
                           "rule the cause in or out."},
    {"id": "B2", "type": "vague", "question": "Why are sales bad?",
     "expected_tools_required": ["detect_anomaly"],
     "expected_behaviour": "Uses the default period (the 8 complete weeks ending {lf}) against the same period last "
                           "year, states that period, and reports the expected facts for it (see default_period)."},
    {"id": "B3", "type": "partial_period", "question": "Why did revenue drop in the week of 2011-12-05?",
     "expected_tools_required": [],
     "expected_behaviour": "Recognises the week is incomplete (data ends {dt}); does not flag it as an anomaly."},
    {"id": "B4", "type": "out_of_range", "question": "Why did revenue drop in November 2009 compared with the year "
                                                     "before?",
     "expected_tools_required": [],
     "expected_behaviour": "Says data starts {ds}, so there is no baseline; no numbers invented."},
    {"id": "B5", "type": "out_of_scope_metric", "question": "Why did our profit margin fall in October 2011?",
     "expected_tools_required": [],
     "expected_behaviour": "Says there is no cost data, so margin cannot be measured; may offer revenue analysis "
                           "instead; data_gaps lists cost of goods."},
]


def main():
    print(f"reading {EXCEL.name} (both sheets) ...")
    lines, stats = load_lines()
    data = Data(lines)
    print(f"  {stats}")
    cases = [analytic_case(data, "A1", pd.Timestamp("2011-09-26"), pd.Timestamp("2011-11-20"),
                           "demo window from data/scenario.yaml, on the REAL data")]
    w8 = sorted(candidate_windows(data, 8), key=lambda x: x[2])
    w4 = sorted(candidate_windows(data, 4), key=lambda x: x[2])
    picks = [(w8[0], "largest 8-week year-over-year decline"), (w4[0], "largest 4-week year-over-year decline"),
             (w8[-1], "largest 8-week year-over-year increase"),
             (min(w8, key=lambda x: abs(x[2])), "flattest 8-week window (no real change)")]
    used = {cases[0]["expected_facts"]["period"]["start"]}
    for i, ((s, e, _), why) in enumerate(picks, start=2):
        if ds_(s) in used:
            continue
        used.add(ds_(s))
        cases.append(analytic_case(data, f"A{i}", s, e, why))
    for w in w8[1:]:                                   # second-largest non-overlapping 8-week decline
        if w[2] < 0 and all(abs((w[0] - pd.Timestamp(c["expected_facts"]["period"]["start"])).days) >= 56 for c in cases):
            cases.append(analytic_case(data, f"A{len(cases) + 1}", w[0], w[1],
                                       "second-largest non-overlapping 8-week decline"))
            break
    default_s = data.lf - 55 * DAY
    default = analytic_case(data, "B2_default", default_s, data.lf, "default period for vague questions")
    fmt = {"lf": ds_(data.lf), "dt": ds_(data.dt), "ds": ds_(data.ds)}
    beh = [{**b, "expected_behaviour": b["expected_behaviour"].format(**fmt)} for b in BEHAVIOUR_CASES]
    beh[1]["default_period"] = default["expected_facts"]

    out = {"source": "data/raw/online_retail_II.xlsx (both sheets), computed with pandas independently of the "
                     "DuckDB pipeline", "dataset_checks": stats, "data_start": fmt["ds"], "data_through": fmt["dt"],
           "last_complete_day": fmt["lf"], "analytic_cases": cases, "behaviour_cases": beh}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "ground_truth.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "ground_truth.md").write_text(to_markdown(out), encoding="utf-8")
    print(f"wrote {len(cases)} analytic + {len(beh)} behaviour cases to {OUT_DIR}")


def to_markdown(o):
    L = ["# WhyBot evaluation Q&A (ground truth)", "",
         f"Computed directly from `{o['source']}`. Data {o['data_start']} → {o['data_through']}; last complete day "
         f"{o['last_complete_day']}. Regenerate: `python src/eval/build_ground_truth.py`.", "",
         f"Dataset checks: {o['dataset_checks']}", "", "## Analytic questions", ""]
    for c in o["analytic_cases"]:
        f, a, dr, mv, co = (c["expected_facts"], c["expected_facts"]["anomaly"], c["expected_facts"]["drivers"],
                            c["expected_facts"]["customer_movement"], c["expected_facts"]["country"])
        L += [f"### {c['id']}. {c['question']}", f"_Selected: {c['why_selected']}_", "",
              f"- **Real change?** {a['direction']}, severity **{a['severity']}** (anomaly: {a['is_anomaly']}); "
              f"net £{a['net_revenue_current']:,.2f} vs £{a['net_revenue_baseline']:,.2f} "
              f"(change £{a['net_change']:,.2f}, raw {a['raw_change_pct']}%, trading-day adjusted "
              f"{a['adjusted_change_pct']}%, z {a['z_score']}; trading days {a['trading_days']['current']} vs "
              f"{a['trading_days']['baseline']})",
              f"- **Where:** top country {co['top_country']} (£{co['top_country_change']:,.2f}, "
              f"{co['top_country_share_of_gross_decline']:.0%} of gross decline); UK £{co['uk_change']:,.2f}, "
              f"international £{co['international_change']:,.2f}",
              f"- **How:** dominant driver **{dr['dominant_driver']}**; effects {dr['effects']}",
              f"- **Who:** lost {mv['lost']['customers']} (£{mv['lost']['revenue_change']:,.2f}), new "
              f"{mv['new']['customers']} (£{mv['new']['revenue_change']:,.2f}), retained {mv['retained']['customers']}"
              f" (£{mv['retained']['revenue_change']:,.2f})",
              f"- **Tools required:** {', '.join(c['expected_tools_required'])}",
              f"- **Expected behaviour:** {c['expected_behaviour']}", ""]
    L += ["## Behaviour questions", ""]
    for b in o["behaviour_cases"]:
        L += [f"### {b['id']} ({b['type']}). {b['question']}", f"- **Expected behaviour:** {b['expected_behaviour']}",
              ""]
    return "\n".join(L)


if __name__ == "__main__":
    main()
