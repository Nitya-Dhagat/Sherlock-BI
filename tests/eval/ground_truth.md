# WhyBot evaluation Q&A (ground truth)

Computed directly from `data/raw/online_retail_II.xlsx (both sheets), computed with pandas independently of the DuckDB pipeline`. Data 2009-12-01 → 2011-12-09; last complete day 2011-12-04. Regenerate: `python src/eval/build_ground_truth.py`.

Dataset checks: {'raw_rows': 1067371, 'product_line_rows': 1021124, 'bulk_reversal_rows': 4, 'net_revenue_total': 18926266.18}

## Analytic questions

### A1. Why did revenue drop between 2011-09-26 and 2011-11-20 compared with last year?
_Selected: demo window from data/scenario.yaml, on the REAL data_

- **Real change?** decline, severity **none** (anomaly: False); net £2,207,758.64 vs £2,257,638.78 (change £-49,880.14, raw -2.21%, trading-day adjusted -2.21%, z -0.34; trading days 48 vs 48)
- **Where:** top country United Kingdom (£-96,199.36, 68% of gross decline); UK £-96,199.36, international £46,319.22
- **How:** dominant driver **orders_per_customer**; effects {'active_customers': -39210.33, 'orders_per_customer': -44532.22, 'aov': 63.57, 'unattributed': 33798.84}
- **Who:** lost 1076 (£-532,493.96), new 1156 (£627,859.24), retained 1299 (£-179,044.26)
- **Tools required:** detect_anomaly
- **Expected behaviour:** Say the change is within normal variation (severity none); do not invent a cause. Further drill-down is optional.

### A2. Why did revenue drop between 2011-02-14 and 2011-04-10 compared with last year?
_Selected: largest 8-week year-over-year decline_

- **Real change?** decline, severity **high** (anomaly: True); net £1,129,962.58 vs £1,238,893.35 (change £-108,930.77, raw -8.79%, trading-day adjusted -14.49%, z -4.35; trading days 48 vs 45)
- **Where:** top country United Kingdom (£-136,842.75, 77% of gross decline); UK £-136,842.75, international £27,911.98
- **How:** dominant driver **aov**; effects {'active_customers': -9272.79, 'orders_per_customer': -60632.89, 'aov': -63418.12, 'unattributed': 24393.03}
- **Who:** lost 717 (£-356,453.23), new 848 (£408,034.52), retained 782 (£-184,905.09)
- **Tools required:** detect_anomaly, drill_down, driver_decomposition, customer_movement
- **Expected behaviour:** Confirm a real decline (high severity). Locate it (top country United Kingdom), explain HOW (dominant driver: aov) and WHO (lost vs new customers), then test the top segment's significance. Report 'driven by'/'consistent with', not proof.

### A3. Why did revenue drop between 2011-03-14 and 2011-04-10 compared with last year?
_Selected: largest 4-week year-over-year decline_

- **Real change?** decline, severity **high** (anomaly: True); net £594,674.83 vs £621,676.81 (change £-27,001.98, raw -4.34%, trading-day adjusted -16.3%, z -2.37; trading days 24 vs 21)
- **Where:** top country United Kingdom (£-45,867.54, 72% of gross decline); UK £-45,867.54, international £18,865.56
- **How:** dominant driver **aov**; effects {'active_customers': 14561.25, 'orders_per_customer': -19295.63, 'aov': -35008.63, 'unattributed': 12741.03}
- **Who:** lost 416 (£-189,349.97), new 643 (£295,788.12), retained 540 (£-146,181.16)
- **Tools required:** detect_anomaly, drill_down, driver_decomposition, customer_movement
- **Expected behaviour:** Confirm a real decline (high severity). Locate it (top country United Kingdom), explain HOW (dominant driver: aov) and WHO (lost vs new customers), then test the top segment's significance. Report 'driven by'/'consistent with', not proof.

### A4. Why did revenue rise between 2011-08-01 and 2011-09-25 compared with last year?
_Selected: largest 8-week year-over-year increase_

- **Real change?** increase, severity **high** (anomaly: True); net £1,521,771.72 vs £1,260,615.67 (change £261,156.05, raw 20.72%, trading-day adjusted 20.72%, z 2.52; trading days 47 vs 47)
- **Where:** top country United Kingdom (£202,151.48, 0% of gross decline); UK £202,151.48, international £59,004.57
- **How:** dominant driver **aov**; effects {'active_customers': 66367.99, 'orders_per_customer': -30648.49, 'aov': 186194.66, 'unattributed': 39241.89}
- **Who:** lost 656 (£-310,545.31), new 927 (£577,528.62), retained 914 (£-45,069.15)
- **Tools required:** detect_anomaly, drill_down, driver_decomposition, customer_movement
- **Expected behaviour:** Confirm a real increase (high severity). Locate it (top country United Kingdom), explain HOW (dominant driver: aov) and WHO (lost vs new customers), then test the top segment's significance. Report 'driven by'/'consistent with', not proof.

### A5. Why did revenue change between 2010-12-20 and 2011-02-13 compared with last year?
_Selected: flattest 8-week window (no real change)_

- **Real change?** flat, severity **none** (anomaly: False); net £859,506.55 vs £858,903.81 (change £602.74, raw 0.07%, trading-day adjusted 0.07%, z -0.52; trading days 39 vs 39)
- **Where:** top country United Kingdom (£11,715.00, 0% of gross decline); UK £11,715.00, international £-11,112.26
- **How:** dominant driver **unattributed**; effects {'active_customers': 3586.74, 'orders_per_customer': -21928.63, 'aov': -44259.09, 'unattributed': 63203.72}
- **Who:** lost 493 (£-224,834.65), new 609 (£323,935.15), retained 578 (£-161,701.48)
- **Tools required:** detect_anomaly
- **Expected behaviour:** Say the change is within normal variation (severity none); do not invent a cause. Further drill-down is optional.

## Behaviour questions

### B1 (unanswerable). Did a competitor take our customers?
- **Expected behaviour:** Headline says the data cannot show competitor activity; may describe what the data does show; competitor ideas only under hypotheses; data_gaps lists competitor data. Must not rule the cause in or out.

### B2 (vague). Why are sales bad?
- **Expected behaviour:** Uses the default period (the 8 complete weeks ending 2011-12-04) against the same period last year, states that period, and reports the expected facts for it (see default_period).

### B3 (partial_period). Why did revenue drop in the week of 2011-12-05?
- **Expected behaviour:** Recognises the week is incomplete (data ends 2011-12-09); does not flag it as an anomaly.

### B4 (out_of_range). Why did revenue drop in November 2009 compared with the year before?
- **Expected behaviour:** Says data starts 2009-12-01, so there is no baseline; no numbers invented.

### B5 (out_of_scope_metric). Why did our profit margin fall in October 2011?
- **Expected behaviour:** Says there is no cost data, so margin cannot be measured; may offer revenue analysis instead; data_gaps lists cost of goods.
