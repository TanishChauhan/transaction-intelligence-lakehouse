# BUILD SPEC — Transaction Intelligence Lakehouse (Databricks Free Edition)

A near-real-time **financial transaction intelligence platform** for a fictional
bank/payments company. A synthetic source system emits card transactions; the platform
ingests them, refines them through a **medallion lakehouse** (bronze → silver → gold),
derives **rule-based fraud signals** plus customer/merchant analytics, and serves a
Databricks SQL dashboard.

This is a **data engineering** project, not an ML project. We build the *platform that
powers* fraud analytics — ingestion, conformance, feature/aggregation logic, rule-based
scoring, and serving. We do **not** train an ML model.

The same architecture doubles as a retail order-analytics platform, so domain logic
(fraud rules, spend marts) is kept cleanly separated from the generic pipeline so it can
be relabeled later.

---

## Hard platform constraints (Databricks Free Edition)

- **Serverless compute only.** No custom clusters, no instance configs, no GPU.
- **Python and SQL only.** No Scala, no RDDs. Use the PySpark DataFrame API.
- **No cloud mounts and no external buckets.** All storage is **Unity Catalog managed
  tables** and **Unity Catalog Volumes** (`/Volumes/...`). The landing zone for the
  generator is a UC Volume, not a cloud bucket.
- **One workspace, one metastore.** Single catalog for the project.
- **Daily compute quotas + a single 2X-Small SQL warehouse.** Keep data volumes modest
  (thousands–low millions of rows). Streaming jobs MUST use `trigger(availableNow=True)`
  (micro-batch then stop) — never an always-on continuous stream.
- **Restricted outbound internet.** The generator runs as a job/notebook task writing to
  a Volume.
- Non-commercial use; this is a portfolio project.

Use `/Volumes/<catalog>/<schema>/<volume>/...` paths for all file IO. Use Delta as the
table format (Unity Catalog managed Delta tables).

---

## Target tech stack (do not substitute without asking)

| Concern        | Tool                                                          |
|----------------|---------------------------------------------------------------|
| Source sim     | Python + Faker (writes JSON to a UC Volume landing path)      |
| Ingestion      | Spark Structured Streaming + **Auto Loader** (directory-listing mode), `trigger(availableNow=True)` → Bronze Delta |
| Lakehouse      | Delta Lake on Databricks Free Edition                          |
| Governance     | Unity Catalog (catalog + bronze/silver/gold schemas + Volume) |
| Transformation | **dbt Core** (`dbt-databricks` adapter) — silver + gold        |
| Data quality   | dbt tests (not_null/unique/relationships/accepted_values) + custom tests; bronze quarantine path |
| Orchestration  | **Databricks Asset Bundles (DAB)** → a Databricks Workflows job chaining the tasks |
| IaC            | **Terraform** (`databricks` provider) — catalog, schemas, volume, grants |
| CI/CD          | **GitHub Actions** — dbt build + pytest on PR; `databricks bundle deploy` on main |
| Serving        | Databricks SQL dashboard (built in-product; document the queries) |
| Optional AI    | Genie over gold tables, or a small LangGraph NL→SQL agent      |

---

## Data model

### Reference dimensions (generated once)
- **customers**: `customer_id`, `name`, `home_country`, `home_city`, `home_lat`,
  `home_lon`, `account_open_date`, `typical_txn_amount` (per-customer baseline mean),
  `typical_txn_std`.
- **merchants**: `merchant_id`, `merchant_name`, `merchant_category` (MCC-like enum:
  grocery, fuel, electronics, travel, gambling, crypto, atm, online_marketplace),
  `merchant_country`, `merchant_city`, `merchant_lat`, `merchant_lon`,
  `risk_tier` (low/medium/high).

### Transaction event (streamed JSON)
- `transaction_id` (uuid), `event_timestamp` (ISO8601), `customer_id`, `card_id`,
  `merchant_id`, `merchant_category`, `amount` (decimal), `currency`,
  `txn_country`, `txn_city`, `txn_lat`, `txn_lon`,
  `channel` (POS | ECOMMERCE | ATM), `device_id` (nullable, ecommerce only),
  `is_fraud_label` (boolean — injected ground truth).

`is_fraud_label` is **ground truth for validation only**. The pipeline derives its own
`fraud_score`/flags from features. **Detection logic must never read `is_fraud_label`.**

### Injected fraud patterns
1. **Velocity** — N rapid transactions on one card within a short window.
2. **Impossible travel** — two transactions in geographically distant locations within an
   implausibly short time gap.
3. **Amount anomaly** — amount far above the customer's `typical_txn_amount` baseline.
4. **High-risk merchant** — disproportionate transactions at `risk_tier = high` merchants.
5. **Card testing** — several tiny transactions followed by a large one.

Fraud ratio is configurable (default ~1–2% of events).

---

## Medallion layers

- **Bronze** (`bronze.transactions_raw`): raw ingested events, append-only, with ingestion
  metadata (`_ingested_at`, `_source_file`). Malformed records routed to a quarantine
  table/path. Minimal transformation — schema-on-read, preserve raw.
- **Silver** (dbt): deduplicated, type-cast, validated, conformed transactions joined to
  customer/merchant dimensions. One clean `silver_transactions` model. dbt tests enforce
  not_null keys, unique `transaction_id`, accepted values for `channel`/`merchant_category`,
  referential integrity to dims.
- **Gold** (dbt): star-schema marts + analytics:
  - `dim_customer`, `dim_merchant`, `fct_transaction`.
  - `fraud_signals` — per-transaction features (velocity count in rolling window,
    geo-distance/speed since prior txn, amount z-score vs customer baseline, merchant risk
    flag) and a composite **rule-based `fraud_score`** + boolean flags. SQL window
    functions; no ML.
  - `customer_spend_daily` — daily spend/txn-count per customer.
  - `merchant_risk_daily` — daily flagged-transaction counts and rates per merchant.
  - A `_gold.yml` `exposures` entry for the dashboard.

---

## Build phases

- **Phase 0 — Scaffold.** Repo structure, `pyproject.toml`, README skeleton, `docs/architecture.md` stub, Free Edition setup notes.
- **Phase 1 — Synthetic source.** `reference_data.py`, `generate_transactions.py`, `config.py`, 5 injected fraud patterns, `tests/test_generator.py`. Runnable locally (`./_landing`) and on Databricks (`/Volumes/...`).
- **Phase 2 — Terraform.** `databricks` provider: catalog, bronze/silver/gold schemas, landing Volume, grants.
- **Phase 3 — Bronze ingestion.** Auto Loader (`cloudFiles`, directory listing) → `bronze.transactions_raw`, `trigger(availableNow=True)`, checkpoint on Volume, quarantine bad records.
- **Phase 4 — dbt silver.** dbt project init, bronze as source, `stg_transactions`, `silver_transactions`, `_silver.yml` tests, `profiles.yml.example`.
- **Phase 5 — dbt gold.** `dim_customer`, `dim_merchant`, `fct_transaction`, `fraud_signals`, `customer_spend_daily`, `merchant_risk_daily`, `_gold.yml` tests + exposures, singular recall-sanity test.
- **Phase 6 — Orchestration (DAB).** `databricks.yml` + job: `generate_batch → bronze_ingest → dbt_build → quality_gate`, serverless, paused schedule.
- **Phase 7 — CI/CD.** GitHub Actions: PR runs pytest + dbt build (dev schema); main runs `databricks bundle deploy`.
- **Phase 8 — Dashboard (documented).** SQL queries + in-product build walkthrough.
- **Phase 9 — Optional AI.** Genie docs + optional LangGraph NL→SQL agent in `ai/`.
- **Phase 10 — Docs.** README finalize (Mermaid diagram), `docs/architecture.md`.

---

## Coding standards

- Python: type hints, docstrings, `black`-formatted, config-driven (no magic constants),
  no hardcoded paths/secrets.
- SQL/dbt: `ref()`/`source()` everywhere, no hardcoded catalog/schema, models documented
  in `.yml`, every model has at least one test.
- Idempotent and re-runnable. Streaming uses checkpoints; dbt incremental where sensible
  (`fct_transaction`, `fraud_signals` on `event_timestamp`).
- Small, reviewable commits per phase.

## Do NOT

- No Kafka, local Postgres sink, Docker, or always-on local service.
- No external S3/ADLS buckets or cloud mounts.
- No Scala, RDDs, GPU, or custom clusters.
- No continuous streaming — `availableNow` micro-batch only.
- Detection logic must not read `is_fraud_label`.
- Do not commit tokens, profiles with secrets, or `.tfstate`.

## Deliverable

A public GitHub repo a reviewer understands in 5 minutes: synthetic source → Auto Loader
bronze → dbt silver/gold medallion → rule-based fraud signals + analytics marts →
orchestrated job → IaC + CI/CD → SQL dashboard, all on free, cloud-native infrastructure.
