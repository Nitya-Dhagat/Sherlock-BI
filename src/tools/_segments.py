"""
WhyBot — src/tools/_segments.py

Shared SQL plumbing for Mugdha's three tools, written against the REAL schema in
Nisaj's `data/clean/whybot.duckdb` (built by src/build_data.py then src/build_clean.py).

Why this file exists
--------------------
The base table is `product_lines`, not `transactions`. It is already filtered to
product rows (`is_product_line AND NOT is_bulk_reversal`), so the tools must NOT
re-apply that filter. It also carries precomputed `line_revenue`, `sale_date`,
`week_start` and a boolean `is_cancel`.

The harder part: `customer_type` is NOT a column anywhere. In build_clean.py it is
produced by `customer_features_asof(con, as_of)` — computed as of a date so there is
no look-ahead. Any tool filtering on customer_type therefore has to derive it inside
its own query, as of the START of the comparison baseline, and join it on.

CUTOFF CONSTANTS BELOW MIRROR build_clean.py. They must stay identical. If Nisaj
changes WHOLESALE_UNITS_CUTOFF, this file changes in the same PR, or your segment
stops meaning what his drill_down says it means.
"""

from __future__ import annotations

# ---- mirrored from src/build_clean.py --------------------------------------
WHOLESALE_UNITS_CUTOFF = 150
MIN_ORDERS_FOR_TYPE = 2

# dimensions that are plain columns on product_lines
SIMPLE_DIMENSIONS = {
    "country", "is_uk", "stock_code", "description", "invoice",
    "customer_id", "month", "source_sheet",
}
# dimensions this module synthesises
DERIVED_DIMENSIONS = {"customer_type", "tenure"}
KNOWN_DIMENSIONS = SIMPLE_DIMENSIONS | DERIVED_DIMENSIONS


class UnknownDimension(ValueError):
    pass


def lines_cte(as_of: str) -> str:
    """
    Returns a WITH-body defining `lines`: product_lines plus as-of customer_type and
    tenure. `as_of` should be the START of the baseline period, so the classification
    uses only information available before either window — no look-ahead.

    Values match build_clean.py exactly: wholesale_like / retail_like /
    unclassified_new (underscores, not hyphens).
    """
    return f"""
    cust_asof AS (
        SELECT customer_id,
               count(DISTINCT invoice) AS prior_orders,
               sum(quantity) / nullif(count(DISTINCT invoice), 0) AS avg_units_per_order,
               min(sale_date) AS first_order_date
        FROM product_lines
        WHERE NOT is_cancel
          AND customer_id IS NOT NULL
          AND sale_date < DATE '{as_of}'
        GROUP BY 1
    ),
    cust_typed AS (
        SELECT customer_id,
               CASE WHEN prior_orders < {MIN_ORDERS_FOR_TYPE} THEN 'unclassified_new'
                    WHEN avg_units_per_order >= {WHOLESALE_UNITS_CUTOFF} THEN 'wholesale_like'
                    ELSE 'retail_like' END AS customer_type,
               CASE WHEN first_order_date IS NULL THEN 'new' ELSE 'returning' END AS tenure
        FROM cust_asof
    ),
    lines AS (
        SELECT p.*,
               coalesce(t.customer_type, 'unclassified_new') AS customer_type,
               coalesce(t.tenure, 'new') AS tenure
        FROM product_lines p
        LEFT JOIN cust_typed t USING (customer_id)
    )
    """


def filters_sql(filters: list[dict] | None) -> tuple[str, list]:
    """
    Turns [{"dimension": "country", "value": "United Kingdom"}] into a SQL fragment.
    Raises UnknownDimension rather than silently producing a binder error, so the
    agent gets a structured error it can act on.
    """
    if not filters:
        return "", []
    clauses, params = [], []
    for f in filters:
        dim = f.get("dimension")
        if dim not in KNOWN_DIMENSIONS:
            raise UnknownDimension(
                f"unknown dimension '{dim}'; known: {sorted(KNOWN_DIMENSIONS)}"
            )
        clauses.append(f'"{dim}" = ?')
        params.append(f["value"])
    return " AND " + " AND ".join(clauses), params


def not_filters_sql(filters: list[dict] | None) -> tuple[str, list]:
    """Negation of the above — used by forecast_scenario's what-if."""
    if not filters:
        return "", []
    clauses, params = [], []
    for f in filters:
        dim = f.get("dimension")
        if dim not in KNOWN_DIMENSIONS:
            raise UnknownDimension(f"unknown dimension '{dim}'")
        clauses.append(f'NOT ("{dim}" = ?)')
        params.append(f["value"])
    return " AND " + " AND ".join(clauses), params


# revenue expressions — line_revenue is precomputed in product_lines
NET_REVENUE = "sum(line_revenue)"
GROSS_REVENUE = "coalesce(sum(line_revenue) FILTER (WHERE NOT is_cancel), 0)"


def revenue_expr(metric: str) -> str:
    return GROSS_REVENUE if metric == "gross_revenue" else NET_REVENUE
