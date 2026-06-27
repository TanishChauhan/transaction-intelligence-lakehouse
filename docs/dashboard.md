# Dashboard — Transaction Intelligence Lakehouse (Phase 8)

This is the build walkthrough for the **fraud-monitoring + detection-validation** dashboard,
built in-product with **Databricks SQL / Lakeview** over the project's `gold` marts. The
dashboard itself is created in the Databricks UI; this repo only versions the **queries**
(`dashboards/*.sql`) and this walkthrough — not a JSON dashboard export.

## Purpose

The dashboard answers two questions:

1. **What is happening?** — headline KPIs, flagged-rate trend over time, which detection
   signals fire most, the fraud-score distribution, and the riskiest merchants / most-flagged
   customers (operational fraud monitoring).
2. **Is detection any good?** — a confusion matrix with precision, recall and flag rate
   (detection validation), comparing the rule-based predictions against ground-truth labels.

### Gold (and silver) tables that feed it

| Table | Used by tiles |
|---|---|
| `gold.fraud_signals` | 01 KPIs, 02 flagged-rate trend, 03 signal breakdown, 04 score distribution, 06 top flagged customers, 08 validation |
| `gold.fct_transaction` | 01 KPIs (total transacted amount) |
| `gold.merchant_risk_daily` | 05 top risky merchants |
| `gold.dim_merchant` | 05 top risky merchants (name / category / risk_tier) |
| `gold.dim_customer` | 06 top flagged customers (name / home_country) |
| `gold.customer_spend_daily` | 07 daily spend trend |
| `silver.silver_transactions` | 08 validation only (the **only** tile that reads `is_fraud_label`) |

## How detection works (read this first)

- Detection is **100% rule-based SQL** — no ML. `gold.fraud_signals` derives five boolean
  flags from observable features computed with window functions:
  `flag_velocity` (rapid txns per card), `flag_impossible_travel` (geo speed > 900 km/h),
  `flag_amount_anomaly` (amount z-score vs the customer baseline), `flag_high_risk_merchant`
  (merchant risk tier), and `flag_card_testing` (tiny probe txns then a large charge).
- A weighted composite `fraud_score` (0.00–1.00) is rolled up from those flags, and
  `is_flagged_fraud = fraud_score >= 0.25`.
- **`is_fraud_label` is ground truth used only to VALIDATE recall / precision** (tile 08).
  It must never participate in detection — `fraud_signals` and `fct_transaction` are
  intentionally clean of it, and only `08_detection_validation.sql` reads it (by joining to
  `silver.silver_transactions`).

## Data freshness / orchestration

The dashboard reads the **gold tables directly**; it does not run the pipeline. Those tables
are refreshed by the Phase 6 orchestration job
(`generate_batch → bronze_ingest → dbt_build → quality_gate`), whose schedule is **paused** on
Databricks Free Edition. To refresh the numbers, trigger that bundle job (or run `dbt build`)
and then re-run the dashboard; otherwise the tiles reflect the last successful build.

---

## Setup

### 1. Create / pick a SQL warehouse

1. In the Databricks workspace, go to **SQL → SQL Warehouses** and create (or start) a
   serverless SQL warehouse.
2. Open **SQL → SQL Editor** (or **Dashboards → Create dashboard → Data**) and select that
   warehouse as the compute for your queries.

### 2. Set the default catalog

All queries reference schema-qualified names (`gold.<table>`, `silver.silver_transactions`).
Set the warehouse / query default **catalog** to the project catalog (default
**`txn_intelligence`**) so those names resolve:

```sql
USE CATALOG txn_intelligence;
```

Alternatively, prefix every table explicitly, e.g. `txn_intelligence.gold.fraud_signals` and
`txn_intelligence.silver.silver_transactions`.

### 3. Build the dashboard

Create a new Lakeview dashboard. For each query below: add a **dataset** (paste the SQL from the
named file), add a **widget/tile**, pick the suggested visualization, and map the columns. Then
add the recommended dashboard filters and bind them to the relevant datasets.

---

## Tiles

### 01 — KPI Overview
- **File:** `dashboards/01_kpi_overview.sql`
- **Visualization:** Counter tiles (one per metric): `total_transactions`,
  `flagged_transactions`, `flagged_rate` (format as %), `total_amount`, `distinct_flagged_cards`.
- **Filters:** None (dataset-wide by design). This query has no date column, so date filtering
  applies to the time-series tiles (02 and 07) instead.

### 02 — Flagged Rate Over Time
- **File:** `dashboards/02_flagged_rate_over_time.sql`
- **Visualization:** Combo chart — `activity_date` on X; `txn_count` and `flagged_count` as
  bars; `flagged_rate` as a line on a secondary axis.
- **Filters:** date range on `activity_date` (derived from `event_timestamp`).

### 03 — Signal Breakdown
- **File:** `dashboards/03_signal_breakdown.sql`
- **Visualization:** Horizontal bar chart — `signal_name` (Y) vs `flagged_count` (X).
- **Filters:** none required; optionally a global date filter if you parameterize the query.

### 04 — Fraud Score Distribution
- **File:** `dashboards/04_fraud_score_distribution.sql`
- **Visualization:** Bar chart / histogram — `score_bucket` (X) vs `txn_count` (Y).
- **Filters:** none required.

### 05 — Top Risky Merchants
- **File:** `dashboards/05_top_risky_merchants.sql`
- **Visualization:** Table — columns `merchant_name`, `merchant_category`, `risk_tier`,
  `total_txn_count`, `total_flagged_count`, `flagged_rate` (format as %). Sort by `flagged_rate`.
- **Filters:** `merchant_category`, `risk_tier` (from `dim_merchant`); date range on
  `merchant_risk_daily.activity_date` if you parameterize the query.

### 06 — Top Flagged Customers
- **File:** `dashboards/06_top_flagged_customers.sql`
- **Visualization:** Table — columns `customer_name`, `home_country`, `total_txn_count`,
  `flagged_txn_count`, `flagged_rate` (format as %). Sort by `flagged_txn_count`.
- **Filters:** `home_country` (from `dim_customer`).

### 07 — Daily Spend Trend
- **File:** `dashboards/07_daily_spend_trend.sql`
- **Visualization:** Line chart — `spend_date` on X; `total_spend` (and optionally `txn_count`)
  on Y.
- **Filters:** date range on `spend_date`.

### 08 — Detection Validation
- **File:** `dashboards/08_detection_validation.sql`
- **Visualization:** Counter tiles for `precision`, `recall`, `flag_rate`, plus a small table
  for the confusion matrix (`true_positives`, `false_positives`, `false_negatives`,
  `true_negatives`).
- **Filters:** none required (whole-dataset validation).
- **Note:** this is the **only** tile that reads `is_fraud_label`, and it does so for
  validation analytics only — never for detection.

---

## Suggested layout

Top row: **01 KPI Overview** counters. Second row: **02 Flagged Rate Over Time** (wide).
Third row: **03 Signal Breakdown** + **04 Fraud Score Distribution** side by side.
Fourth row: **05 Top Risky Merchants** + **06 Top Flagged Customers** tables.
Fifth row: **07 Daily Spend Trend** (wide). Bottom: **08 Detection Validation** counters +
confusion-matrix table.

Add a shared **date range** filter where the underlying dataset exposes a date column, then bind
it per the per-tile notes above.
