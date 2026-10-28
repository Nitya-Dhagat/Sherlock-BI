"""
make_scenario.py  -  the labeled SYNTHETIC demo scenario ("we planted a cause and WhyBot recovered it").

Run from the repo root (after build_clean.py):
    python src/make_scenario.py                       # defaults: 30 UK wholesale-like accounts stop ordering after 2011-09-26
    python src/make_scenario.py --n 40 --mode reduce --keep 0.25

What it does (the REAL data is never modified):
  1. Picks the accounts: UK, wholesale-like (avg units/order >= 150, >= 3 orders in the 365 days before the
     cutoff) AND active in last year's matching window; top N by revenue in that window.
  2. Writes a scenario copy of the raw transactions in which those accounts' invoices on/after the cutoff are
     removed (mode "lost") or thinned to a fraction (mode "reduce").
  3. Rebuilds every table on the copy with build_clean.main()  ->  data/scenario/whybot_scenario.duckdb
  4. Measures the effect and writes data/scenario.yaml: what was changed, the demo question and the
     EXPECTED ANSWER (written before the agent runs).

Point the tools/agent at the demo data with the environment variable WHYBOT_DB=data/scenario/whybot_scenario.duckdb
(real data stays in data/clean/whybot.duckdb).
"""
import argparse
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from build_clean import main as build_clean_main, sql, esc

ROOT = Path(__file__).resolve().parent.parent
REAL_DIR = ROOT / "data" / "clean"
REAL_DB = REAL_DIR / "whybot.duckdb"
RAW_PARQUET = REAL_DIR / "transactions_raw.parquet"
SCEN_DIR = ROOT / "data" / "scenario"
SCEN_RAW = SCEN_DIR / "transactions_raw_scenario.parquet"
SCEN_DB_NAME = "whybot_scenario.duckdb"
YAML_PATH = ROOT / "data" / "scenario.yaml"


def iso(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def period_net(con, start, end):
    q = sql("SELECT coalesce(sum(line_revenue), 0) FROM product_lines "
            "WHERE sale_date BETWEEN DATE '__S__' AND DATE '__E__'", S=start, E=end)
    return float(con.execute(q).fetchone()[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2011-09-26", help="first day the planted change applies")
    ap.add_argument("--period-start", default="2011-09-26")
    ap.add_argument("--period-end", default="2011-11-20")
    ap.add_argument("--n", type=int, default=30, help="number of planted accounts")
    ap.add_argument("--mode", choices=["lost", "reduce"], default="lost")
    ap.add_argument("--keep", type=float, default=0.25, help="share of invoices kept in reduce mode")
    ap.add_argument("--min-units", type=float, default=150, help="wholesale-like cutoff: avg units per order")
    a = ap.parse_args()

    base_start = iso(pd.Timestamp(a.period_start) - pd.Timedelta(days=364))   # same period last year
    base_end = iso(pd.Timestamp(a.period_end) - pd.Timedelta(days=364))

    # ---- 1. choose the accounts (from the REAL tables) ----------------------------------------
    real = duckdb.connect(str(REAL_DB), read_only=True)
    picked = real.execute(sql("""
        WITH ttm AS (
            SELECT customer_id, arg_max(country, order_ts) AS country, count(*) AS orders_ttm,
                   sum(units) * 1.0 / count(*) AS avg_units
            FROM customer_orders
            WHERE order_date >= DATE '__C__' - INTERVAL 365 DAY AND order_date < DATE '__C__'
            GROUP BY 1),
        base AS (
            SELECT customer_id, sum(order_value) AS rev_base
            FROM customer_orders
            WHERE order_date BETWEEN DATE '__BS__' AND DATE '__BE__'
            GROUP BY 1)
        SELECT t.customer_id, round(b.rev_base, 2) AS rev_last_year_window, t.orders_ttm,
               round(t.avg_units, 1) AS avg_units_per_order
        FROM ttm t JOIN base b USING (customer_id)
        WHERE t.country = 'United Kingdom' AND t.orders_ttm >= 3 AND t.avg_units >= __U__
        ORDER BY b.rev_base DESC
        LIMIT __N__
    """, C=a.cutoff, BS=base_start, BE=base_end, U=a.min_units, N=a.n)).df()
    if len(picked) < a.n:
        print(f"WARNING: only {len(picked)} accounts qualify (asked for {a.n}). Lower --min-units or --n.")
    if picked.empty:
        raise SystemExit("No qualifying accounts. Did build_clean.py run?")
    ids = [int(x) for x in picked["customer_id"]]
    id_list = ",".join(str(i) for i in ids)

    # ---- 2. scenario copy of the raw transactions ------------------------------------------------
    SCEN_DIR.mkdir(parents=True, exist_ok=True)
    keep_clause = ""
    if a.mode == "reduce":
        keep_clause = f"AND hash(invoice) % 100 >= {int(round(a.keep * 100))}"
    con = duckdb.connect()
    con.execute(sql("""
        COPY (
            SELECT * FROM read_parquet('__RAW__')
            WHERE NOT (
                coalesce(CASE WHEN isnan(customer_id) THEN NULL ELSE CAST(customer_id AS BIGINT) END IN (__IDS__), false)
                AND invoice_date >= TIMESTAMP '__C__ 00:00:00'
                __KEEP__
            )
        ) TO '__OUT__' (FORMAT PARQUET)
    """, RAW=esc(RAW_PARQUET), IDS=id_list, C=a.cutoff, KEEP=keep_clause, OUT=esc(SCEN_RAW)))
    n_raw = con.execute(sql("SELECT count(*) FROM read_parquet('__P__')", P=esc(RAW_PARQUET))).fetchone()[0]
    n_scen = con.execute(sql("SELECT count(*) FROM read_parquet('__P__')", P=esc(SCEN_RAW))).fetchone()[0]
    con.close()
    print(f"scenario raw rows: {n_scen:,} (real: {n_raw:,}; removed {n_raw - n_scen:,})")

    # ---- 3. rebuild every table on the scenario copy -----------------------------------------------
    build_clean_main(raw_parquet=SCEN_RAW, out_dir=SCEN_DIR, db_name=SCEN_DB_NAME)

    # ---- 4. measure the planted effect -------------------------------------------------------------
    scen = duckdb.connect(str(SCEN_DIR / SCEN_DB_NAME), read_only=True)
    base = period_net(real, base_start, base_end)
    real_p = period_net(real, a.period_start, a.period_end)
    scen_p = period_net(scen, a.period_start, a.period_end)
    planted = scen_p - real_p                       # negative = revenue removed by the plant
    real_yoy, scen_yoy = (real_p / base - 1) * 100, (scen_p / base - 1) * 100
    total_change = scen_p - base
    share = planted / total_change if total_change < 0 else float("nan")
    group_share_real = (real_p - period_net_excluding(real, ids, a.period_start, a.period_end)) / real_p
    scen.close()
    real.close()

    print("\n=== SCENARIO RESULT ===")
    print(f"period {a.period_start} .. {a.period_end} vs {base_start} .. {base_end}")
    print(f"last-year window net revenue : {base:,.0f}")
    print(f"real data this period        : {real_p:,.0f}  ({real_yoy:+.1f}% YoY)")
    print(f"scenario data this period    : {scen_p:,.0f}  ({scen_yoy:+.1f}% YoY)")
    print(f"revenue removed by the plant : {planted:,.0f}  (planted group = {group_share_real:.1%} of real period revenue)")
    print(f"planted share of the scenario decline: {share:.0%}" if share == share else
          "scenario shows no net decline: increase --n or lower --keep")
    if scen_yoy > -8:
        print("HINT: the drop is small (< 8% YoY). Raise --n (e.g. 45) or use --mode lost for a clearer signal.")
    if share == share and share < 0.5:
        print("HINT: the plant explains < 50% of the decline. Raise --n, or accept that other segments also fell.")

    # ---- 5. scenario.yaml -------------------------------------------------------------------------------
    lines = [
        "# SYNTHETIC SCENARIO. The real data in data/clean is untouched. State this openly in the pitch.",
        "synthetic: true",
        f"created: {date.today().isoformat()}",
        f"database: data/scenario/{SCEN_DB_NAME}   # set WHYBOT_DB to this path for the demo",
        "change:",
        f"  mode: {a.mode}" + ("   # all invoices on/after the cutoff removed" if a.mode == 'lost'
                               else f"   # only {int(round(a.keep*100))}% of invoices kept (hash of invoice id)"),
        f"  cutoff: {a.cutoff}",
        "  group: UK, wholesale-like accounts (avg units/order >= %g, >= 3 orders in prior 365 days), "
        "active in last year's matching window; top %d by that window's revenue" % (a.min_units, len(ids)),
        f"  accounts: {len(ids)}",
        f"  customer_ids: [{', '.join(str(i) for i in ids)}]",
        f"  rows_removed: {n_raw - n_scen}",
        "demo:",
        f"  period: {{start: {a.period_start}, end: {a.period_end}}}",
        "  compare_to: same_period_last_year",
        '  question: "Why did revenue drop between %s and %s compared with last year?"' % (a.period_start, a.period_end),
        "  backup_no_anomaly: \"Why did revenue drop in <a quiet window you verify in weekly_metrics.csv>?\"",
        "  backup_unanswerable: \"Did a competitor take our customers?\"",
        "measured:",
        f"  last_year_window_net_revenue: {base:.0f}",
        f"  real_this_period_net_revenue: {real_p:.0f}",
        f"  scenario_this_period_net_revenue: {scen_p:.0f}",
        f"  scenario_yoy_pct: {scen_yoy:.1f}",
        f"  revenue_removed_by_plant: {planted:.0f}",
        "expected_answer:   # written BEFORE the agent runs",
        "  headline: Net revenue fell year over year because a group of UK wholesale-like accounts stopped ordering."
        if a.mode == "lost" else
        "  headline: Net revenue fell year over year because a group of UK wholesale-like accounts ordered far less.",
        "  segment: customer_type = wholesale_like (UK)",
        f"  rough_share_of_decline: {share:.2f}" if share == share else "  rough_share_of_decline: n/a",
        "  driver: " + ("fewer active customers (lost accounts); AOV roughly unchanged"
                        if a.mode == "lost" else "fewer orders per customer; AOV roughly unchanged"),
        "  customer_movement: lost customers should carry most of the decline" if a.mode == "lost"
        else "  customer_movement: retained customers' revenue falls",
        "  not_expected: seasonality, trading days or price changes as the main cause",
        "  confidence: should be Medium or High",
    ]
    YAML_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {YAML_PATH.relative_to(ROOT)}")
    print(picked.head(10).to_string(index=False))


def period_net_excluding(con, ids, start, end):
    q = sql("SELECT coalesce(sum(line_revenue), 0) FROM product_lines "
            "WHERE sale_date BETWEEN DATE '__S__' AND DATE '__E__' "
            "AND NOT coalesce(customer_id IN (__IDS__), false)",
            S=start, E=end, IDS=",".join(str(i) for i in ids))
    return float(con.execute(q).fetchone()[0])


if __name__ == "__main__":
    main()