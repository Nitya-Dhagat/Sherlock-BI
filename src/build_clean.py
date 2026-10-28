"""
build_clean.py  -  step 2 of the WhyBot data layer.

Run from the repo root (after build_data.py):   python src/build_clean.py

Reads   data/clean/transactions_raw.parquet
Writes  data/clean/transactions_clean.parquet   all de-duplicated rows with audit flags
        data/clean/product_lines.parquet        the rows every metric uses (share this one)
        data/clean/whybot.duckdb                tables below
        data/clean/daily_metrics.csv, weekly_metrics.csv   (send weekly_metrics.csv to Mugdha)
        data/clean/clean_report.json            numbers for the README / data-quirks note

DuckDB tables created:
  transactions_clean   all non-duplicate rows + flags (is_cancel, is_product_line, is_bulk_reversal ...)
  product_lines        metric rows: product-like code, price > 0, not a bulk buy-and-cancel pair
  daily_metrics        complete daily calendar (zero days included)
  weekly_metrics       complete ISO-week calendar (zero weeks included), wow/yoy, partial flags
  customer_orders      one row per customer x invoice (non-cancel)
  customer_first_order first/last order date per customer (tenure, new vs returning)
  product_summary      per stock code: description, first/last sale, units, revenue, n_prices
  product_weekly       per stock code x week: units, revenue, avg_price, orders

Function for the tools:  customer_features_asof(con, as_of)  -> RFM, basket size, customer_type (no look-ahead)
"""
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CLEAN_DIR = ROOT / "data" / "clean"
RAW_PARQUET = CLEAN_DIR / "transactions_raw.parquet"
CLEAN_PARQUET = CLEAN_DIR / "transactions_clean.parquet"
LINES_PARQUET = CLEAN_DIR / "product_lines.parquet"
DUCKDB_PATH = CLEAN_DIR / "whybot.duckdb"
REPORT_PATH = CLEAN_DIR / "clean_report.json"

# ---- cleaning constants (documented in README) ----
PRODUCT_CODE_REGEX = r"^[0-9]{5}[A-Z]{0,2}$"   # real product codes; drops POST, DOT, M, D, AMAZONFEE, B, GIFT_*, DCGS*, ...
BULK_REVERSAL_QTY = 10000                       # buy-and-cancel pairs of at least this many units are excluded
WHOLESALE_UNITS_CUTOFF = 150                    # avg units per order >= cutoff -> wholesale_like (median is ~155)
MIN_ORDERS_FOR_TYPE = 2                         # fewer prior orders -> unclassified_new


def sql(query, **kw):
    """Tiny template helper (avoids f-string clashes with regex braces)."""
    for k, v in kw.items():
        query = query.replace(f"__{k}__", str(v))
    return query


def esc(path):
    return str(path).replace("\\", "/").replace("'", "''")


# =====================================================================================
def main():
    if not RAW_PARQUET.exists():
        raise FileNotFoundError(f"{RAW_PARQUET} not found. Run src/build_data.py first.")

    con = duckdb.connect(str(DUCKDB_PATH))
    report = {}

    # ---- 1. stage: normalise types, give every row an id --------------------------------
    con.execute(sql("""
        CREATE OR REPLACE TABLE stage AS
        SELECT
            row_number() OVER () AS row_id,
            trim(invoice) AS invoice,
            upper(trim(stock_code)) AS stock_code,
            trim(description) AS description,
            quantity,
            invoice_date,
            unit_price,
            CASE WHEN isnan(customer_id) THEN NULL ELSE CAST(customer_id AS BIGINT) END AS customer_id,
            trim(country) AS country,
            source_sheet
        FROM read_parquet('__RAW__')
    """, RAW=esc(RAW_PARQUET)))
    report["raw_rows"] = con.execute("SELECT count(*) FROM stage").fetchone()[0]

    # ---- 2. exact duplicates (keep first; the Dec-2010 sheet overlap lives here) --------
    con.execute("""
        CREATE OR REPLACE TABLE flagged AS
        SELECT *,
            row_number() OVER (
                PARTITION BY invoice, stock_code, description, quantity, invoice_date,
                             unit_price, customer_id, country
                ORDER BY source_sheet, row_id
            ) AS dup_rank
        FROM stage
    """)
    report["duplicate_rows_removed"] = con.execute("SELECT count(*) FROM flagged WHERE dup_rank > 1").fetchone()[0]
    report["duplicate_revenue_removed"] = float(con.execute(
        "SELECT coalesce(sum(quantity*unit_price),0) FROM flagged WHERE dup_rank > 1").fetchone()[0])

    # ---- 3. audit flags -------------------------------------------------------------------
    con.execute(sql("""
        CREATE OR REPLACE TABLE flagged2 AS
        SELECT row_id, invoice, stock_code, description, quantity, invoice_date, unit_price,
               customer_id, country, source_sheet,
               invoice LIKE 'C%' AS is_cancel,
               (regexp_matches(stock_code, '__RX__') AND unit_price > 0) AS is_product_line,
               quantity * unit_price AS line_revenue,
               customer_id IS NOT NULL AS is_attributed,
               CAST(invoice_date AS DATE) AS sale_date
        FROM flagged
        WHERE dup_rank = 1
    """, RX=PRODUCT_CODE_REGEX))

    # ---- 4. bulk buy-and-cancel pairs (same customer, code, price, qty; order then cancel) ----
    con.execute(sql("""
        CREATE OR REPLACE TABLE bulk_reversal_rows AS
        WITH pairs AS (
            SELECT c.row_id AS c_id, o.row_id AS o_id
            FROM flagged2 c
            JOIN flagged2 o
              ON c.customer_id = o.customer_id
             AND c.stock_code = o.stock_code
             AND c.unit_price = o.unit_price
             AND o.quantity = -c.quantity
             AND o.invoice_date <= c.invoice_date
            WHERE c.is_cancel AND NOT o.is_cancel
              AND c.is_product_line AND o.is_product_line
              AND c.quantity <= -__Q__
        )
        SELECT c_id AS row_id FROM pairs
        UNION
        SELECT o_id AS row_id FROM pairs
    """, Q=BULK_REVERSAL_QTY))

    con.execute("""
        CREATE OR REPLACE TABLE transactions_clean AS
        SELECT f.*, (b.row_id IS NOT NULL) AS is_bulk_reversal
        FROM flagged2 f
        LEFT JOIN bulk_reversal_rows b USING (row_id)
    """)

    # ---- 5. the metric rows ----------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE product_lines AS
        SELECT row_id, invoice, stock_code, description, quantity, invoice_date, sale_date,
               CAST(date_trunc('week', sale_date) AS DATE) AS week_start,
               strftime(sale_date, '%Y-%m') AS month,
               unit_price, line_revenue, customer_id, is_attributed, is_cancel,
               country, (country = 'United Kingdom') AS is_uk, source_sheet
        FROM transactions_clean
        WHERE is_product_line AND NOT is_bulk_reversal
    """)

    con.execute(sql("COPY transactions_clean TO '__P__' (FORMAT PARQUET)", P=esc(CLEAN_PARQUET)))
    con.execute(sql("COPY product_lines TO '__P__' (FORMAT PARQUET)", P=esc(LINES_PARQUET)))

    # cleaning report numbers
    r = con.execute("""
        SELECT count(*) AS kept_rows,
               count(*) FILTER (WHERE NOT is_product_line) AS non_product_rows,
               coalesce(sum(line_revenue) FILTER (WHERE NOT is_product_line), 0) AS non_product_revenue,
               count(*) FILTER (WHERE is_bulk_reversal) AS bulk_reversal_rows
        FROM transactions_clean
    """).fetchone()
    report.update(rows_after_dedupe=r[0], non_product_rows_excluded=r[1],
                  non_product_revenue_excluded=float(r[2]), bulk_reversal_rows_excluded=r[3])
    bulk = con.execute("""
        SELECT invoice, stock_code, quantity, customer_id FROM transactions_clean
        WHERE is_bulk_reversal ORDER BY invoice_date
    """).df()
    report["bulk_reversal_lines"] = bulk.astype(str).to_dict("records")

    # ---- 6. daily metrics (complete calendar) ----------------------------------------------
    daily = con.execute("""
        SELECT sale_date,
               sum(line_revenue) AS net_revenue,
               coalesce(sum(line_revenue) FILTER (WHERE NOT is_cancel), 0) AS gross_revenue,
               coalesce(sum(line_revenue) FILTER (WHERE is_cancel), 0) AS cancellation_revenue,
               coalesce(sum(line_revenue) FILTER (WHERE NOT is_attributed), 0) AS unattributed_revenue,
               count(DISTINCT invoice) FILTER (WHERE NOT is_cancel) AS orders,
               count(DISTINCT customer_id) FILTER (WHERE NOT is_cancel) AS active_customers,
               count(*) AS lines
        FROM product_lines GROUP BY 1 ORDER BY 1
    """).df()
    daily["sale_date"] = pd.to_datetime(daily["sale_date"])
    min_date, max_date = daily["sale_date"].min(), daily["sale_date"].max()
    daily = daily.set_index("sale_date").reindex(pd.date_range(min_date, max_date, freq="D"))
    daily.index.name = "sale_date"
    daily = daily.fillna(0)
    daily["trading_day"] = (daily["gross_revenue"] > 0).astype(int)
    daily["attributed_net_revenue"] = daily["net_revenue"] - daily["unattributed_revenue"]
    daily["aov"] = daily["gross_revenue"] / daily["orders"].replace(0, np.nan)
    daily["week_start"] = daily.index - pd.to_timedelta(daily.index.dayofweek, unit="D")
    daily["is_partial_day"] = daily.index == max_date
    daily = daily.reset_index()

    # ---- 7. weekly metrics (complete ISO-week calendar) -------------------------------------
    weekly = con.execute("""
        SELECT week_start,
               sum(line_revenue) AS net_revenue,
               coalesce(sum(line_revenue) FILTER (WHERE NOT is_cancel), 0) AS gross_revenue,
               coalesce(sum(line_revenue) FILTER (WHERE is_cancel), 0) AS cancellation_revenue,
               coalesce(sum(line_revenue) FILTER (WHERE NOT is_attributed), 0) AS unattributed_revenue,
               count(DISTINCT invoice) FILTER (WHERE NOT is_cancel) AS orders,
               count(DISTINCT customer_id) FILTER (WHERE NOT is_cancel) AS active_customers,
               count(DISTINCT sale_date) FILTER (WHERE NOT is_cancel) AS trading_days
        FROM product_lines GROUP BY 1 ORDER BY 1
    """).df()
    weekly["week_start"] = pd.to_datetime(weekly["week_start"])
    weekly = weekly.set_index("week_start").reindex(
        pd.date_range(weekly["week_start"].min(), weekly["week_start"].max(), freq="7D"))
    weekly.index.name = "week_start"
    weekly = weekly.fillna(0)
    weekly["attributed_net_revenue"] = weekly["net_revenue"] - weekly["unattributed_revenue"]
    weekly["aov"] = weekly["gross_revenue"] / weekly["orders"].replace(0, np.nan)
    weekly["wow_pct"] = (weekly["net_revenue"].pct_change() * 100).replace([np.inf, -np.inf], np.nan)
    weekly["yoy_pct"] = ((weekly["net_revenue"] / weekly["net_revenue"].shift(52) - 1) * 100) \
        .replace([np.inf, -np.inf], np.nan)
    weekly["is_partial_week"] = (weekly.index < min_date) | (weekly.index + pd.Timedelta(days=6) > max_date)
    iso = weekly.index.isocalendar()
    weekly["iso_year"], weekly["iso_week"] = iso["year"].values, iso["week"].values
    weekly = weekly.reset_index()

    for name, frame, key in (("daily_metrics", daily, "sale_date"), ("weekly_metrics", weekly, "week_start")):
        con.register("frame_tmp", frame)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS "
                    f"SELECT CAST({key} AS DATE) AS {key}, * EXCLUDE ({key}) FROM frame_tmp")
        con.unregister("frame_tmp")
        frame.to_csv(CLEAN_DIR / f"{name}.csv", index=False)

    # ---- 8. customer tables ------------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE customer_orders AS
        SELECT customer_id, invoice,
               min(invoice_date) AS order_ts,
               CAST(min(invoice_date) AS DATE) AS order_date,
               arg_max(country, invoice_date) AS country,
               sum(line_revenue) AS order_value,
               sum(quantity) AS units,
               count(*) AS lines
        FROM product_lines
        WHERE is_attributed AND NOT is_cancel
        GROUP BY customer_id, invoice
    """)
    con.execute("""
        CREATE OR REPLACE TABLE customer_first_order AS
        SELECT customer_id, min(order_date) AS first_order_date, max(order_date) AS last_order_date,
               count(*) AS lifetime_orders
        FROM customer_orders GROUP BY customer_id
    """)

    # ---- 9. product tables --------------------------------------------------------------------
    con.execute("""
        CREATE OR REPLACE TABLE product_summary AS
        SELECT stock_code,
               mode(description) AS description,
               min(sale_date) FILTER (WHERE NOT is_cancel) AS first_sale_date,
               max(sale_date) FILTER (WHERE NOT is_cancel) AS last_sale_date,
               sum(quantity) FILTER (WHERE NOT is_cancel) AS total_units,
               sum(line_revenue) AS net_revenue,
               count(DISTINCT unit_price) FILTER (WHERE NOT is_cancel) AS n_prices
        FROM product_lines GROUP BY stock_code
    """)
    con.execute("""
        CREATE OR REPLACE TABLE product_weekly AS
        SELECT stock_code, week_start,
               coalesce(sum(quantity) FILTER (WHERE NOT is_cancel), 0) AS units,
               coalesce(sum(line_revenue) FILTER (WHERE NOT is_cancel), 0) AS gross_revenue,
               sum(line_revenue) AS net_revenue,
               sum(line_revenue) FILTER (WHERE NOT is_cancel)
                   / NULLIF(sum(quantity) FILTER (WHERE NOT is_cancel), 0) AS avg_price,
               count(DISTINCT invoice) FILTER (WHERE NOT is_cancel) AS orders
        FROM product_lines GROUP BY stock_code, week_start
    """)

    # ---- 10. reconciliation checks (a number that does not add up is a bug) -------------------
    tot = con.execute("""
        SELECT sum(line_revenue), sum(line_revenue) FILTER (WHERE NOT is_cancel),
               sum(line_revenue) FILTER (WHERE NOT is_attributed)
        FROM product_lines
    """).fetchone()
    net, gross, unattr = float(tot[0]), float(tot[1]), float(tot[2])
    checks = {
        "raw = dedupe + duplicates": report["raw_rows"] == report["rows_after_dedupe"] + report["duplicate_rows_removed"],
        "daily net sums to total": np.isclose(daily["net_revenue"].sum(), net),
        "weekly net sums to total": np.isclose(weekly["net_revenue"].sum(), net),
        "daily gross sums to total": np.isclose(daily["gross_revenue"].sum(), gross),
        "weekly unattributed sums to total": np.isclose(weekly["unattributed_revenue"].sum(), unattr),
        "attributed + unattributed = net": np.isclose(
            weekly["attributed_net_revenue"].sum() + weekly["unattributed_revenue"].sum(), net),
        "weekly calendar is gap-free": bool((weekly["week_start"].diff().dropna() == pd.Timedelta(days=7)).all()),
    }
    report.update(net_revenue=net, gross_revenue=gross, unattributed_revenue=unattr,
                  unattributed_share=unattr / net, final_product_line_rows=int(
                      con.execute("SELECT count(*) FROM product_lines").fetchone()[0]),
                  data_start=str(min_date.date()), data_end=str(max_date.date()),
                  checks={k: bool(v) for k, v in checks.items()})

    for t in ("stage", "flagged", "flagged2", "bulk_reversal_rows"):
        con.execute(f"DROP TABLE IF EXISTS {t}")

    # ---- 11. print summary + demo of the as-of customer features ------------------------------
    print("\n=== CLEAN REPORT ===")
    for k, v in report.items():
        if k not in ("checks", "bulk_reversal_lines"):
            print(f"{k:34s} {v:,.2f}" if isinstance(v, float) else f"{k:34s} {v}")
    print("\nbulk reversal lines excluded:")
    print(bulk.to_string(index=False))
    print("\n=== RECONCILIATION CHECKS ===")
    for k, v in checks.items():
        print(("PASS  " if v else "FAIL  ") + k)
    print("\npartial weeks flagged:", weekly.loc[weekly["is_partial_week"], "week_start"].dt.date.astype(str).tolist())
    print("zero-sales weeks (shutdowns):", weekly.loc[weekly["gross_revenue"] == 0, "week_start"].dt.date.astype(str).tolist())

    demo = customer_features_asof(con, "2011-09-26")
    print("\n=== customer_features_asof(2011-09-26) ===")
    print("customers:", len(demo))
    print(demo["customer_type"].value_counts().to_string())
    print(demo["rfm_tier"].value_counts().to_string())
    print(demo.head(5).to_string())

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {REPORT_PATH}")
    con.close()
    if not all(checks.values()):
        raise SystemExit("A reconciliation check FAILED. Fix before sharing the data.")
    print("Build completed successfully.")


# =====================================================================================
def customer_features_asof(con, as_of):
    """Customer features using ONLY orders strictly before `as_of` (no look-ahead).

    Columns: customer_id, country, is_uk, frequency, gross_revenue, net_revenue, units,
             first_order_date, last_order_date, recency_days, tenure_days,
             avg_units_per_order, avg_order_value, customer_type,
             r_score, f_score, m_score, rfm_score, rfm_tier
    customer_type: wholesale_like (avg units/order >= WHOLESALE_UNITS_CUTOFF),
                   retail_like, or unclassified_new (< MIN_ORDERS_FOR_TYPE prior orders)
    rfm_tier: high (score >= 12), mid (8-11), low (<= 7); scores are quintiles 1-5 (5 = best)
    Customers with no order before `as_of` are not returned (they are 'new' in the period).
    """
    as_of_ts = pd.Timestamp(as_of)
    as_of_sql = as_of_ts.strftime("%Y-%m-%d")
    df = con.execute(sql("""
        WITH o AS (
            SELECT customer_id,
                   arg_max(country, order_ts) AS country,
                   count(*) AS frequency,
                   sum(order_value) AS gross_revenue,
                   sum(units) AS units,
                   min(order_date) AS first_order_date,
                   max(order_date) AS last_order_date
            FROM customer_orders
            WHERE order_date < DATE '__D__'
            GROUP BY customer_id
        ),
        m AS (
            SELECT customer_id, sum(line_revenue) AS net_revenue
            FROM product_lines
            WHERE is_attributed AND sale_date < DATE '__D__'
            GROUP BY customer_id
        )
        SELECT o.*, m.net_revenue FROM o JOIN m USING (customer_id)
    """, D=as_of_sql)).df()
    if df.empty:
        return df
    df["first_order_date"] = pd.to_datetime(df["first_order_date"])
    df["last_order_date"] = pd.to_datetime(df["last_order_date"])
    df["is_uk"] = df["country"].eq("United Kingdom")
    df["recency_days"] = (as_of_ts - df["last_order_date"]).dt.days
    df["tenure_days"] = (as_of_ts - df["first_order_date"]).dt.days
    df["avg_units_per_order"] = df["units"] / df["frequency"]
    df["avg_order_value"] = df["gross_revenue"] / df["frequency"]
    df["customer_type"] = np.select(
        [df["frequency"] < MIN_ORDERS_FOR_TYPE, df["avg_units_per_order"] >= WHOLESALE_UNITS_CUTOFF],
        ["unclassified_new", "wholesale_like"], default="retail_like")

    def quintile(series, ascending):
        ranks = series.rank(method="first", ascending=ascending)
        k = min(5, len(series))
        return pd.qcut(ranks, k, labels=range(1, k + 1)).astype(int)

    df["r_score"] = quintile(df["recency_days"], ascending=False)   # most recent = 5
    df["f_score"] = quintile(df["frequency"], ascending=True)
    df["m_score"] = quintile(df["net_revenue"], ascending=True)
    df["rfm_score"] = df["r_score"] + df["f_score"] + df["m_score"]
    df["rfm_tier"] = np.select([df["rfm_score"] >= 12, df["rfm_score"] >= 8], ["high", "mid"], default="low")
    return df


if __name__ == "__main__":
    main()