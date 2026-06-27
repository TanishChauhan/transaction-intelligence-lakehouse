# Databricks Genie over the Gold Layer

> **Optional, governed self-serve analytics.** This walkthrough sets up a
> [Databricks Genie](https://docs.databricks.com/aws/en/genie/) space over the
> curated **gold** tables of the Transaction Intelligence Lakehouse. Genie
> complements the BI dashboard: the dashboard answers the *known* questions;
> Genie lets reviewers ask their *own* questions in plain English and get
> trustworthy SQL + results back.

## What Genie is

Genie is Databricks' natural-language analytics experience. You point it at a set
of Unity Catalog tables, give it written context ("space instructions") and a few
**trusted/sample SQL queries**, and business users can then ask questions in
plain English. Genie generates SQL, runs it on a SQL warehouse, shows the result,
and lets users inspect and correct the SQL — all under Unity Catalog governance
(permissions, lineage, audit).

## Why the gold tables are a good Genie domain

Genie is only as good as the data and context you give it. The gold layer is an
ideal domain because it is:

- **Clean & conformed** — one row per grain, friendly column names, no raw noise.
- **Documented** — every model and column carries descriptions (persisted via
  dbt `+persist_docs`), which Genie surfaces as table/column metadata.
- **Tested** — dbt tests (uniqueness, not-null, relationships, a recall-sanity
  test) mean answers rest on validated data.
- **Governed & safe** — gold is intentionally free of the `is_fraud_label`
  ground truth, so self-serve users only ever see serving-safe, rule-based
  signals.

## Gold tables to add

| Table                  | Grain                     | What it answers                                  |
| ---------------------- | ------------------------- | ------------------------------------------------ |
| `dim_customer`         | one row per customer      | Who the customer is, home location, baselines    |
| `dim_merchant`         | one row per merchant      | Merchant name, category, country, risk tier      |
| `fct_transaction`      | one row per transaction   | The transaction events (amount, channel, time)   |
| `fraud_signals`        | one row per transaction   | Rule-based flags, fraud score, `is_flagged_fraud`|
| `customer_spend_daily` | one row per customer-day  | Daily spend (txn count, total, average)          |
| `merchant_risk_daily`  | one row per merchant-day  | Daily flagged volume and flagged rate            |

All live in catalog `txn_intelligence`, schema `gold` (the catalog is overridable
via the `TIL_CATALOG` env var in the dbt project).

## Step-by-step setup

### 1. Prerequisites

- A running **SQL warehouse** (Serverless or Pro) the space can query.
- The gold tables built and present in Unity Catalog (`dbt build` against the
  gold models).
- Genie enabled for the workspace, and you have `CAN MANAGE` on the warehouse and
  `SELECT` on the gold tables.

### 2. Create a Genie space

1. In the Databricks workspace, open **Genie** (left nav, under *SQL* /
   *AI/BI*).
2. Click **New → Genie space**.
3. Name it e.g. **"Transaction Intelligence — Gold"** and pick the SQL warehouse
   that will run the queries.

### 3. Add the gold tables

1. In the space's **Data** panel, click **Add tables**.
2. Browse to `txn_intelligence.gold` and select all six tables:
   `dim_customer`, `dim_merchant`, `fct_transaction`, `fraud_signals`,
   `customer_spend_daily`, `merchant_risk_daily`.
3. Confirm. Genie ingests their column metadata and descriptions automatically.

### 4. Write space instructions

Space instructions are free-text context that steer Genie. Add something like:

```text
This space answers questions about retail card transactions and rule-based fraud
detection.

Grain & joins:
- fct_transaction is one row per transaction; join to dim_customer on customer_id
  and dim_merchant on merchant_id.
- fraud_signals is one row per transaction (join to fct_transaction on
  transaction_id). The headline fraud indicator is is_flagged_fraud (boolean);
  fraud_score is a 0..1 composite.
- customer_spend_daily and merchant_risk_daily are pre-aggregated daily marts;
  prefer them for trend/time-series questions.

Definitions:
- "Flagged fraud" / "flagged" means is_flagged_fraud = true.
- "Flagged rate" = flagged transactions / total transactions.
- Risk tier is a static merchant attribute (low/medium/high).
- Detection is RULE-BASED (velocity, impossible travel, amount anomaly, high-risk
  merchant, card testing). It is NOT a trained model and is NOT ground truth.

Conventions:
- Use event_timestamp / transaction_date for time filters.
- Default to recent periods (e.g. last 30 days) when a question is open-ended.
- Always produce read-only SELECT queries.
```

### 5. Add sample / "trusted" SQL queries

Trusted queries teach Genie your preferred joins and metric definitions; it reuses
their patterns. Add a handful, for example:

```sql
-- Overall flagged-fraud rate
SELECT
  COUNT(*)                                              AS total_txns,
  SUM(CAST(is_flagged_fraud AS INT))                    AS flagged_txns,
  AVG(CAST(is_flagged_fraud AS INT))                    AS flagged_rate
FROM txn_intelligence.gold.fraud_signals;
```

```sql
-- Flagged rate by merchant category
SELECT
  t.merchant_category,
  COUNT(*)                                  AS txns,
  SUM(CAST(s.is_flagged_fraud AS INT))      AS flagged,
  AVG(CAST(s.is_flagged_fraud AS INT))      AS flagged_rate
FROM txn_intelligence.gold.fct_transaction t
JOIN txn_intelligence.gold.fraud_signals  s USING (transaction_id)
GROUP BY t.merchant_category
ORDER BY flagged_rate DESC;
```

```sql
-- Daily flagged transactions over the last 30 days
SELECT
  activity_date,
  SUM(flagged_txn_count) AS flagged_txns
FROM txn_intelligence.gold.merchant_risk_daily
WHERE activity_date >= current_date() - INTERVAL 30 DAYS
GROUP BY activity_date
ORDER BY activity_date;
```

Tip: mark these as **trusted assets** so Genie favours them and reviewers see a
verified badge.

### 6. Test, then share

Ask a few of the example questions below, verify the generated SQL is correct,
refine the instructions/trusted queries as needed, then **share** the space with
reviewers (read access to the space + the underlying tables/warehouse).

## Example questions for a reviewer

Paste these into the Genie space:

1. **What's the overall flagged-fraud rate?**
2. **Which merchant categories have the highest flagged rate?**
3. **Show daily flagged transactions for the last 30 days.**
4. **Which customers triggered impossible-travel flags?**
5. **What are the top 10 merchants by flagged transaction count?**
6. **What's the average transaction amount by channel?**
7. **Which home countries have the most customers with at least one flagged transaction?**
8. **How does total daily spend trend over the last 30 days?**

## Notes on scope and governance

- **Complementary, not a replacement.** Genie is for *governed self-serve* —
  ad-hoc questions under Unity Catalog permissions, lineage and audit. The curated
  dashboard remains the canonical view of the KPIs.
- **Rule-based signals.** Everything Genie can query in gold is derived from
  **rule-based** fraud features (velocity, impossible travel, amount anomaly,
  high-risk merchant, card testing). It is not a trained classifier.
- **Label is validation-only.** `is_fraud_label` is ground truth that lives in the
  **silver** layer and is used solely for the recall-sanity test. It is
  deliberately excluded from gold (and therefore from Genie) so self-serve
  analytics can never conflate detection signals with ground truth.
