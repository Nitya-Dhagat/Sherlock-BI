"""
WhyBot EDA for UCI Online Retail II.

Run from the repo root:   python src/eda.py
Reads:   data/clean/transactions_raw.parquet
Writes:  data/eda/eda_report.txt  (paste this file back to Claude)
         data/eda/*.csv           (weekly table, customer table, stock-code lists)

Requires: pandas, numpy, duckdb, pyarrow
"""
import io
import re
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PARQUET = Path("data/clean/transactions_raw.parquet")
OUT = Path("data/eda")
OUT.mkdir(parents=True, exist_ok=True)

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_rows", 60)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")

_buf = io.StringIO()


def out(*args):
    text = " ".join(str(a) for a in args)
    print(text)
    _buf.write(text + "\n")


def section(title):
    out("\n" + "=" * 90)
    out(title)
    out("=" * 90)


def show(df, n=20):
    out(df.head(n).to_string())


# ---------------------------------------------------------------- 0. load
section("0. LOAD AND BASIC SHAPE")
df = pd.read_parquet(PARQUET)

# columns come from build_data.py: invoice, stock_code, description, quantity, invoice_date,
# unit_price, customer_id, country, source_sheet
need = ["invoice", "stock_code", "description", "quantity", "invoice_date", "unit_price",
        "customer_id", "country", "source_sheet"]
missing_cols = [c for c in need if c not in df.columns]
assert not missing_cols, f"missing columns in parquet: {missing_cols}"
out("columns:", list(df.columns))
out("dtypes:\n" + df.dtypes.to_string())
df["description"] = df["description"].astype(object)
df["country"] = df["country"].astype(str).str.strip()
df["source_sheet"] = df["source_sheet"].astype(str)
df["invoice"] = df["invoice"].astype(str).str.strip()
df["stock_code"] = df["stock_code"].astype(str).str.strip().str.upper()
df["invoice_date"] = pd.to_datetime(df["invoice_date"])
df["customer_id"] = pd.to_numeric(df["customer_id"], errors="coerce")
df["revenue"] = df["quantity"] * df["unit_price"]

out("rows:", f"{len(df):,}")
out("date range:", df["invoice_date"].min(), "->", df["invoice_date"].max())
out("distinct invoices:", f"{df['invoice'].nunique():,}",
    "| customers:", f"{df['customer_id'].nunique():,}",
    "| stock codes:", f"{df['stock_code'].nunique():,}",
    "| countries:", df["country"].nunique())
out("null counts:\n" + df.isna().sum().to_string())

sheet_cols = ["source_sheet"]
out("rows per sheet:\n" + df["source_sheet"].value_counts().to_string())
out("date range per sheet:\n" + df.groupby("source_sheet")["invoice_date"].agg(["min", "max"]).to_string())

# ---------------------------------------------------------------- 1. duplicates / overlap
section("1. DUPLICATES AND SHEET OVERLAP")
key = ["invoice", "stock_code", "description", "quantity", "invoice_date", "unit_price",
       "customer_id", "country"]
dup_mask = df.duplicated(subset=key, keep="first")
out("exact duplicate rows (all key columns):", f"{dup_mask.sum():,}", f"({dup_mask.mean():.2%})")
dup_rev = df.loc[dup_mask, "revenue"].sum()
out("revenue in those duplicates:", f"{dup_rev:,.0f}")
dec10 = df[(df["invoice_date"] >= "2010-11-25") & (df["invoice_date"] <= "2010-12-15")]
out("rows in 2010-11-25..2010-12-15 window:", f"{len(dec10):,}",
    "| duplicates inside window:", f"{dec10.duplicated(subset=key).sum():,}")
dups_all = df[df.duplicated(subset=key, keep=False)]
n_cross = (dups_all.groupby(key, dropna=False)["source_sheet"].nunique() > 1).sum()
out("duplicated row-groups whose copies sit in DIFFERENT sheets:", f"{n_cross:,}",
    "| duplicated row-groups total:", f"{dups_all.groupby(key, dropna=False).ngroups:,}")

df = df[~dup_mask].copy()
out("rows after dedupe:", f"{len(df):,}  (all analysis below uses deduped data)")

# ---------------------------------------------------------------- 2. invoice prefixes
section("2. INVOICE PREFIXES (C = cancellation, A = adjustment)")
df["prefix"] = np.where(df["invoice"].str.match(r"^[A-Za-z]"), df["invoice"].str[0].str.upper(), "none")
g = df.groupby("prefix").agg(rows=("invoice", "size"), invoices=("invoice", "nunique"),
                             revenue=("revenue", "sum"), units=("quantity", "sum"))
show(g)
out("invoice length counts:\n" + df["invoice"].str.len().value_counts().to_string())

df["month"] = df["invoice_date"].dt.to_period("M").astype(str)
cm = df.pivot_table(index="month", columns="prefix", values="revenue", aggfunc="sum").fillna(0)
cm["cancel_share_of_gross"] = (-cm.get("C", 0)) / cm.get("none", 1)
out("\ncancellation revenue share of regular revenue, by month:")
show(cm[["cancel_share_of_gross"]], 30)

# do cancellations match an earlier purchase (same customer + stock code)?
canc = df[df["prefix"] == "C"].dropna(subset=["customer_id"])
purch = df[(df["prefix"] == "none") & df["customer_id"].notna()][["customer_id", "stock_code", "invoice_date"]]
first_purchase = purch.groupby(["customer_id", "stock_code"])["invoice_date"].min().rename("first_buy")
m = canc.join(first_purchase, on=["customer_id", "stock_code"])
out("\ncancellation lines with a customer: ", f"{len(canc):,}",
    "| with an earlier purchase of same customer+stock_code:",
    f"{(m['first_buy'] < m['invoice_date']).sum():,}",
    f"({(m['first_buy'] < m['invoice_date']).mean():.1%})")

# ---------------------------------------------------------------- 3. stock codes
section("3. NON-PRODUCT STOCK CODES")
product_like = df["stock_code"].str.match(r"^\d{5}[A-Z]{0,2}$")
out("rows with product-like code (5 digits + optional letters):", f"{product_like.mean():.2%}")
nonprod = (df[~product_like].groupby("stock_code")
           .agg(rows=("invoice", "size"), revenue=("revenue", "sum"),
                units=("quantity", "sum"),
                example_desc=("description", lambda s: s.dropna().iloc[0] if s.notna().any() else ""))
           .sort_values("rows", ascending=False))
out("distinct non-product-like codes:", len(nonprod))
show(nonprod, 40)
nonprod.to_csv(OUT / "nonproduct_codes.csv")
out("\ntotal revenue in non-product-like codes:", f"{nonprod['revenue'].sum():,.0f}",
    "| share of all revenue:", f"{nonprod['revenue'].sum() / df['revenue'].sum():.2%}")

# ---------------------------------------------------------------- 4. price/quantity oddities
section("4. QUANTITY AND PRICE ODDITIES")
out("unit_price <= 0 rows:", f"{(df['unit_price'] <= 0).sum():,}",
    "| price == 0:", f"{(df['unit_price'] == 0).sum():,}",
    "| price < 0:", f"{(df['unit_price'] < 0).sum():,}")
zero_price = df[df["unit_price"] == 0]
out("zero-price rows with customer_id:", f"{zero_price['customer_id'].notna().mean():.1%}",
    "| sample descriptions:", zero_price["description"].dropna().value_counts().head(8).to_dict())
neg_q_regular = df[(df["quantity"] < 0) & (df["prefix"] == "none")]
out("negative quantity on NON-cancellation invoices:", f"{len(neg_q_regular):,}",
    "| of which price == 0:", f"{(neg_q_regular['unit_price'] == 0).sum():,}")
pos_q_canc = df[(df["quantity"] > 0) & (df["prefix"] == "C")]
out("positive quantity on cancellation invoices:", f"{len(pos_q_canc):,}")
out("\nquantity quantiles (product-like, price>0):")
pl = df[product_like & (df["unit_price"] > 0)]
out(pl["quantity"].quantile([0, .001, .01, .25, .5, .75, .99, .999, 1]).to_string())
out("\nunit_price quantiles (product-like, price>0):")
out(pl["unit_price"].quantile([0, .01, .25, .5, .75, .99, .999, 1]).to_string())
out("\ntop 10 extreme quantity lines:")
show(df.reindex(df["quantity"].abs().sort_values(ascending=False).index)
     [["invoice", "stock_code", "quantity", "unit_price", "customer_id", "invoice_date"]], 10)
out("\ntop 10 extreme price lines (product-like):")
show(pl.sort_values("unit_price", ascending=False)
     [["invoice", "stock_code", "quantity", "unit_price", "customer_id"]], 10)

# define the working "product line" frame used below
prod = df[product_like & (df["unit_price"] > 0)].copy()
prod["is_cancel"] = prod["prefix"] == "C"
out("\nPRODUCT-LINE frame rows:", f"{len(prod):,}",
    "| net revenue:", f"{prod['revenue'].sum():,.0f}",
    "| gross revenue:", f"{prod.loc[~prod['is_cancel'], 'revenue'].sum():,.0f}")

# ---------------------------------------------------------------- 5. descriptions
section("5. DESCRIPTIONS")
out("blank/null descriptions:", f"{df['description'].isna().sum():,}",
    "| of which price == 0:", f"{df.loc[df['description'].isna(), 'unit_price'].eq(0).mean():.1%}")
multi = (prod.dropna(subset=["description"]).groupby("stock_code")["description"].nunique())
out("product codes with >1 distinct description:", f"{(multi > 1).sum():,} of {len(multi):,}")
out("sample multi-description codes:")
for sc in multi[multi > 1].head(5).index:
    out("  ", sc, prod.loc[prod["stock_code"] == sc, "description"].dropna().unique()[:4].tolist())
out("distinct product codes:", prod["stock_code"].nunique(),
    "| distinct descriptions:", prod["description"].nunique())

# ---------------------------------------------------------------- 6. missing customer id
section("6. MISSING customer_id")
prod["no_cust"] = prod["customer_id"].isna()
out("share of product-line ROWS without customer_id:", f"{prod['no_cust'].mean():.2%}")
out("share of product-line REVENUE without customer_id:",
    f"{prod.loc[prod['no_cust'], 'revenue'].sum() / prod['revenue'].sum():.2%}")
by_m = prod.groupby("month").apply(
    lambda x: pd.Series({"rev_share_no_cust": x.loc[x["no_cust"], "revenue"].sum() / x["revenue"].sum(),
                         "row_share_no_cust": x["no_cust"].mean()}), include_groups=False)
show(by_m, 30)
cmp = prod[~prod["is_cancel"]].groupby("no_cust").agg(
    lines=("invoice", "size"), invoices=("invoice", "nunique"),
    avg_line_value=("revenue", "mean"), avg_qty=("quantity", "mean"))
cmp["avg_invoice_value"] = (prod[~prod["is_cancel"]].groupby("no_cust")["revenue"].sum() / cmp["invoices"])
out("\nanonymous (True) vs identified (False) buyers:")
show(cmp)
out("\ncountry mix of anonymous rows (top 5):\n" +
    prod.loc[prod["no_cust"], "country"].value_counts(normalize=True).head(5).to_string())

# ---------------------------------------------------------------- 7. date coverage
section("7. DATE COVERAGE")
daily = prod.groupby(prod["invoice_date"].dt.normalize())["revenue"].sum()
full = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
daily = daily.reindex(full, fill_value=0.0)
out("calendar days in range:", len(full), "| days with sales:", int((daily > 0).sum()))
wd = daily.groupby(daily.index.dayofweek).agg(days="size", days_with_sales=lambda s: (s > 0).sum(),
                                              avg_rev="mean")
wd.index = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
show(wd)
zero_days = daily[daily == 0]
out("\nzero-sales days that are NOT Saturdays (likely holidays/shutdown), first 40:")
out(", ".join(d.strftime("%Y-%m-%d") for d in zero_days.index if d.dayofweek != 5)[:1500])
out("\nfirst/last day of data:", daily.index.min().date(), daily.index.max().date(),
    "| last invoice timestamp:", prod["invoice_date"].max())

# ---------------------------------------------------------------- 8. weekly series
section("8. WEEKLY NET-REVENUE SERIES AND CANDIDATE DIPS")
prod["week"] = prod["invoice_date"].dt.to_period("W-SUN").dt.start_time
wk = prod.groupby("week").agg(
    net_revenue=("revenue", "sum"),
    orders=("invoice", lambda s: s[~s.str.startswith("C")].nunique()),
    active_customers=("customer_id", "nunique"),
    trading_days=("invoice_date", lambda s: s.dt.normalize().nunique()),
)
gross = prod[~prod["is_cancel"]].groupby("week")["revenue"].sum().rename("gross_revenue")
wk = wk.join(gross)
wk["aov"] = wk["gross_revenue"] / wk["orders"]
wk["wow_pct"] = wk["net_revenue"].pct_change() * 100
wk["yoy_pct"] = (wk["net_revenue"] / wk["net_revenue"].shift(52) - 1) * 100
last_week = wk.index.max()
wk["partial_final_week"] = wk.index == last_week
yoy = wk["yoy_pct"].dropna()
wk["yoy_z"] = (wk["yoy_pct"] - yoy.mean()) / yoy.std()
wk.to_csv(OUT / "weekly_metrics.csv")
out("weeks:", len(wk), "| first week:", wk.index.min().date(), "| last (partial?) week:", last_week.date())
out("\nweekly net revenue, full table is in data/eda/weekly_metrics.csv. Quantiles:")
out(wk["net_revenue"].quantile([0, .1, .25, .5, .75, .9, 1]).to_string())
cand = wk[(~wk["partial_final_week"]) & ((wk["yoy_z"] < -1.5) | (wk["wow_pct"] < -35))]
out("\nCANDIDATE DIPS (yoy z < -1.5 or WoW < -35%, excluding final partial week):")
show(cand[["net_revenue", "orders", "active_customers", "aov", "trading_days",
           "wow_pct", "yoy_pct", "yoy_z"]], 40)
out("\nNovember peak weeks:")
show(wk.sort_values("net_revenue", ascending=False)[["net_revenue", "orders", "trading_days"]], 8)

# ---------------------------------------------------------------- 9. country mix
section("9. COUNTRY MIX")
ctry = (prod.groupby("country").agg(revenue=("revenue", "sum"), customers=("customer_id", "nunique"),
                                    invoices=("invoice", "nunique")).sort_values("revenue", ascending=False))
ctry["share"] = ctry["revenue"] / ctry["revenue"].sum()
ctry["cum_share"] = ctry["share"].cumsum()
show(ctry, 15)
out("\ncountries:", len(ctry), "| with >= 50 customers:", int((ctry["customers"] >= 50).sum()),
    "| with >= 10 customers:", int((ctry["customers"] >= 10).sum()))
out("'Unspecified' revenue share:", f"{ctry.loc['Unspecified', 'share']:.3%}" if "Unspecified" in ctry.index else "n/a")
uk_by_year = prod.assign(year=prod["invoice_date"].dt.year, uk=prod["country"].eq("United Kingdom")) \
    .groupby(["year", "uk"])["revenue"].sum().unstack()
uk_by_year["uk_share"] = uk_by_year[True] / (uk_by_year[True] + uk_by_year[False])
out("\nUK share by calendar year:")
show(uk_by_year)

# ---------------------------------------------------------------- 10. customers
section("10. CUSTOMER CONCENTRATION AND PROFILE")
pc = prod[prod["customer_id"].notna()].copy()
cust = pc.groupby("customer_id").agg(
    net_revenue=("revenue", "sum"),
    first_order=("invoice_date", "min"),
    last_order=("invoice_date", "max"),
    orders=("invoice", lambda s: s[~s.str.startswith("C")].nunique()),
    distinct_products=("stock_code", "nunique"),
    countries=("country", "nunique"),
    cancel_lines=("is_cancel", "sum"),
    lines=("invoice", "size"),
)
gross_c = pc[~pc["is_cancel"]].groupby("customer_id").agg(
    gross_revenue=("revenue", "sum"), units=("quantity", "sum"))
cust = cust.join(gross_c)
cust["avg_order_value"] = cust["gross_revenue"] / cust["orders"].replace(0, np.nan)
cust["avg_units_per_order"] = cust["units"] / cust["orders"].replace(0, np.nan)
cust["cancel_line_rate"] = cust["cancel_lines"] / cust["lines"]
cust["tenure_days"] = (cust["last_order"] - cust["first_order"]).dt.days
tot = cust["net_revenue"].sum()
srt = cust["net_revenue"].sort_values(ascending=False)
out("identified customers:", f"{len(cust):,}")
out("top-10 revenue share:", f"{srt.head(10).sum() / tot:.2%}",
    "| top-1%:", f"{srt.head(int(len(srt) * 0.01)).sum() / tot:.2%}",
    "| top-10%:", f"{srt.head(int(len(srt) * 0.10)).sum() / tot:.2%}")
out("\ncustomer net_revenue quantiles (heavy-tailed?):")
out(cust["net_revenue"].quantile([0, .1, .25, .5, .75, .9, .99, 1]).to_string())
out("skew:", f"{cust['net_revenue'].skew():.1f}", "| log-skew:",
    f"{np.log1p(cust['net_revenue'].clip(lower=0)).skew():.2f}")
out("\norders per customer quantiles:")
out(cust["orders"].quantile([.1, .25, .5, .75, .9, .99]).to_string())
out("one-time customers (1 order):", f"{(cust['orders'] == 1).mean():.1%}")
y = pc.assign(year=pc["invoice_date"].dt.year)
years = y.groupby("customer_id")["year"].agg(lambda s: set(s))
out("\ncustomers active in 2010 AND 2011:", f"{sum(1 for s in years if {2010, 2011} <= s):,}",
    "| only 2010:", f"{sum(1 for s in years if s == {2010}):,}",
    "| only 2011:", f"{sum(1 for s in years if s == {2011}):,}")

# wholesale-like vs retail-like cut
out("\navg units per order (customer level) quantiles, for wholesale vs retail cut:")
out(cust["avg_units_per_order"].quantile([.1, .25, .5, .75, .9]).to_string())
out("avg order value quantiles:")
out(cust["avg_order_value"].quantile([.1, .25, .5, .75, .9]).to_string())
out("correlation of avg_order_value with avg_units_per_order:",
    f"{cust[['avg_order_value', 'avg_units_per_order']].corr().iloc[0, 1]:.2f}")
cust.to_csv(OUT / "customer_profile.csv")

# ---------------------------------------------------------------- 11. purchase rhythm
section("11. PURCHASE RHYTHM (decides the 'lost customer' window)")
orders = (pc[~pc["is_cancel"]].assign(d=lambda x: x["invoice_date"].dt.normalize())
          .drop_duplicates(["customer_id", "invoice"])[["customer_id", "d"]]
          .drop_duplicates(["customer_id", "d"]).sort_values(["customer_id", "d"]))
orders["gap"] = orders.groupby("customer_id")["d"].diff().dt.days
gaps = orders["gap"].dropna()
out("distinct order-days:", f"{len(orders):,}", "| gaps:", f"{len(gaps):,}")
out("gap days quantiles (all gaps):")
out(gaps.quantile([.25, .5, .75, .9, .95, .99]).to_string())
per_cust = orders.groupby("customer_id")["gap"].median().dropna()
out("\nper-customer median gap quantiles:")
out(per_cust.quantile([.25, .5, .75, .9, .95]).to_string())
for w in (30, 60, 90, 120, 180):
    out(f"share of all gaps longer than {w} days: {(gaps > w).mean():.1%}")
end = prod["invoice_date"].max()
cust["days_since_last"] = (end - cust["last_order"]).dt.days
out("\ncustomers whose last order was > 90 / 180 / 365 days before data end:",
    f"{(cust['days_since_last'] > 90).mean():.1%} / {(cust['days_since_last'] > 180).mean():.1%} / "
    f"{(cust['days_since_last'] > 365).mean():.1%}")

# ---------------------------------------------------------------- 12. products and prices
section("12. PRODUCTS, PRICES, AVAILABILITY")
pr = prod[~prod["is_cancel"]]
by_prod = pr.groupby("stock_code").agg(revenue=("revenue", "sum"), units=("quantity", "sum"),
                                       first_sale=("invoice_date", "min"), last_sale=("invoice_date", "max"),
                                       n_prices=("unit_price", "nunique"),
                                       median_price=("unit_price", "median"))
by_prod = by_prod.sort_values("revenue", ascending=False)
tot_p = by_prod["revenue"].sum()
out("product codes sold:", f"{len(by_prod):,}",
    "| top-50 revenue share:", f"{by_prod.head(50)['revenue'].sum() / tot_p:.2%}",
    "| top-500 share:", f"{by_prod.head(500)['revenue'].sum() / tot_p:.2%}")
out("share of products with more than one price:", f"{(by_prod['n_prices'] > 1).mean():.1%}",
    "| among top-50 products:", f"{(by_prod.head(50)['n_prices'] > 1).mean():.1%}")
by_prod["lifespan_days"] = (by_prod["last_sale"] - by_prod["first_sale"]).dt.days
out("product lifespan days quantiles:")
out(by_prod["lifespan_days"].quantile([.1, .25, .5, .75, .9]).to_string())
out("products first sold in the last 90 days of data:",
    int((by_prod["first_sale"] > end - pd.Timedelta(days=90)).sum()),
    "| last sold more than 90 days before the end:",
    int((by_prod["last_sale"] < end - pd.Timedelta(days=90)).sum()))
out("\ntop 15 products by revenue:")
top_desc = prod.dropna(subset=["description"]).groupby("stock_code")["description"].agg(lambda s: s.mode().iat[0])
show(by_prod.head(15).join(top_desc)[["description", "revenue", "units", "n_prices", "median_price"]], 15)
by_prod.to_csv(OUT / "product_profile.csv")

# ---------------------------------------------------------------- 13. baskets
section("13. INVOICE / BASKET LEVEL")
inv = pr.groupby("invoice").agg(value=("revenue", "sum"), units=("quantity", "sum"),
                                lines=("stock_code", "nunique"), customer_id=("customer_id", "first"),
                                country=("country", "first"), date=("invoice_date", "min"))
out("invoice value quantiles:")
out(inv["value"].quantile([.1, .25, .5, .75, .9, .99]).to_string())
out("units per invoice quantiles:")
out(inv["units"].quantile([.1, .25, .5, .75, .9, .99]).to_string())
out("lines per invoice quantiles:")
out(inv["lines"].quantile([.1, .25, .5, .75, .9, .99]).to_string())
inv["dow"] = inv["date"].dt.dayofweek
inv["hour"] = inv["date"].dt.hour
out("\ninvoices by weekday (0=Mon):\n" + inv["dow"].value_counts().sort_index().to_string())
out("invoices by hour:\n" + inv["hour"].value_counts().sort_index().to_string())

# ---------------------------------------------------------------- 14. feature candidates
section("14. FEATURE CANDIDATES (inventory of what can be engineered)")
out("""Customer-level (as of a period start, no look-ahead):
  recency_days, frequency (orders), monetary (net/gross revenue), avg_order_value, avg_units_per_order,
  distinct_products, cancel_line_rate, tenure_days, median_gap_days, country, is_uk, is_new/returning, RFM tier
Order-level:
  value, units, lines, weekday, hour, is_cancel, has_customer_id
Product-level:
  revenue, units, n_prices, price change %, first/last sale (launch / dropped), lifespan, category
Calendar / period-level:
  trading_days, ISO week, month, UK holiday flags, days to Christmas, partial-period flag
Weekly aggregates:
  net_revenue, gross_revenue, orders, active_customers, aov, wow_pct, yoy_pct""")

# ---------------------------------------------------------------- save
(OUT / "eda_report.txt").write_text(_buf.getvalue(), encoding="utf-8")
print(f"\nSaved report to {OUT / 'eda_report.txt'}")
