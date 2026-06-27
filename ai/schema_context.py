"""Structured metadata for the Transaction Intelligence Lakehouse *gold* layer.

This module is the single source of truth that the optional NL->SQL agent uses to
ground a large language model in the real shape of the warehouse. It is pure
standard-library Python so it is *always* importable (no third-party deps), which
keeps it trivially unit-testable and lets the rest of the agent fail gracefully
when heavier optional packages (langgraph, langchain, the SQL connector) are
absent.

The gold schema lives in catalog ``txn_intelligence`` (overridable via the
``TIL_CATALOG`` env var) under schema ``gold``. Only read-only, governed gold
tables are exposed here on purpose -- the agent must never see silver/bronze or
the validation-only ``is_fraud_label``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

# Default catalog/schema. The catalog mirrors the dbt project default
# (`TIL_CATALOG` -> `txn_intelligence`); the agent only ever queries `gold`.
DEFAULT_CATALOG: str = os.environ.get("TIL_CATALOG", "txn_intelligence")
GOLD_SCHEMA: str = "gold"


@dataclass(frozen=True)
class Column:
    """A single column exposed to the LLM."""

    name: str
    description: str


@dataclass(frozen=True)
class Table:
    """A gold table: its name, a one-line purpose and its column catalogue."""

    name: str
    description: str
    columns: List[Column] = field(default_factory=list)

    def qualified_name(self, catalog: str = DEFAULT_CATALOG) -> str:
        """Return the fully-qualified ``catalog.schema.table`` identifier."""
        return f"{catalog}.{GOLD_SCHEMA}.{self.name}"


# ---------------------------------------------------------------------------
# Gold schema definition. Column names mirror the dbt models in
# dbt/models/gold/*.sql exactly -- keep them in sync if the models change.
# ---------------------------------------------------------------------------
GOLD_TABLES: Dict[str, Table] = {
    "dim_customer": Table(
        name="dim_customer",
        description="Customer dimension: one row per customer.",
        columns=[
            Column("customer_id", "Surrogate/business key for the customer."),
            Column("customer_name", "Display name of the customer."),
            Column("home_country", "Customer's home country."),
            Column("home_city", "Customer's home city."),
            Column("home_lat", "Latitude of the customer's home location."),
            Column("home_lon", "Longitude of the customer's home location."),
            Column("account_open_date", "Date the customer account was opened."),
            Column("typical_txn_amount", "Baseline mean transaction amount for the customer."),
            Column("typical_txn_std", "Baseline standard deviation of the customer's amounts."),
        ],
    ),
    "dim_merchant": Table(
        name="dim_merchant",
        description="Merchant dimension: one row per merchant.",
        columns=[
            Column("merchant_id", "Surrogate/business key for the merchant."),
            Column("merchant_name", "Display name of the merchant."),
            Column("merchant_category", "Merchant category (e.g. grocery, travel, electronics)."),
            Column("merchant_country", "Country the merchant operates in."),
            Column("merchant_city", "City the merchant operates in."),
            Column("merchant_lat", "Latitude of the merchant location."),
            Column("merchant_lon", "Longitude of the merchant location."),
            Column("risk_tier", "Static merchant risk tier: 'low', 'medium' or 'high'."),
        ],
    ),
    "fct_transaction": Table(
        name="fct_transaction",
        description=(
            "Transaction-grain fact (one row per transaction). Clean of any fraud "
            "label by design."
        ),
        columns=[
            Column("transaction_id", "Unique transaction identifier."),
            Column("customer_id", "FK to dim_customer."),
            Column("card_id", "Identifier of the card used for the transaction."),
            Column("merchant_id", "FK to dim_merchant."),
            Column("merchant_category", "Denormalised merchant category at transaction time."),
            Column("channel", "Transaction channel (e.g. online, in_store, atm)."),
            Column("amount", "Transaction amount in the transaction currency."),
            Column("currency", "ISO currency code of the transaction."),
            Column("event_timestamp", "Timestamp when the transaction occurred."),
            Column("transaction_date", "Calendar date derived from event_timestamp."),
        ],
    ),
    "fraud_signals": Table(
        name="fraud_signals",
        description=(
            "Per-transaction rule-based fraud features and composite score. Detection "
            "is derived purely from observable features, never from the label."
        ),
        columns=[
            Column("transaction_id", "FK to fct_transaction."),
            Column("customer_id", "FK to dim_customer."),
            Column("card_id", "Card used for the transaction."),
            Column("merchant_id", "FK to dim_merchant."),
            Column("event_timestamp", "Timestamp when the transaction occurred."),
            Column("velocity_count_5m", "Number of txns on this card in the trailing 5 minutes."),
            Column("seconds_since_prev_txn", "Seconds elapsed since the previous txn on this card."),
            Column("km_from_prev_txn", "Great-circle distance in km from the previous txn on this card."),
            Column("speed_kmh", "Implied travel speed (km/h) between consecutive txns."),
            Column("amount_zscore", "Amount anomaly vs the customer's spend baseline (z-score)."),
            Column("is_high_risk_merchant", "Whether the merchant is in the 'high' risk tier (boolean)."),
            Column("flag_velocity", "Boolean: rapid-fire txn velocity signal fired."),
            Column("flag_impossible_travel", "Boolean: implied speed exceeds a physical threshold."),
            Column("flag_amount_anomaly", "Boolean: amount far above the customer's baseline."),
            Column("flag_high_risk_merchant", "Boolean: transaction at a high-risk merchant."),
            Column("flag_card_testing", "Boolean: small probe txns followed by a large charge."),
            Column("fraud_score", "Composite weighted score (0..1) over the boolean flags."),
            Column("is_flagged_fraud", "Boolean: fraud_score >= 0.25 (the headline flagged-fraud indicator)."),
        ],
    ),
    "customer_spend_daily": Table(
        name="customer_spend_daily",
        description="Daily spend aggregates per customer (one row per customer per day).",
        columns=[
            Column("customer_id", "FK to dim_customer."),
            Column("spend_date", "Calendar day of the aggregate."),
            Column("txn_count", "Number of transactions for the customer on that day."),
            Column("total_amount", "Sum of transaction amounts for the customer on that day."),
            Column("avg_amount", "Average transaction amount for the customer on that day."),
        ],
    ),
    "merchant_risk_daily": Table(
        name="merchant_risk_daily",
        description="Daily merchant risk aggregates (one row per merchant per day).",
        columns=[
            Column("merchant_id", "FK to dim_merchant."),
            Column("activity_date", "Calendar day of the aggregate."),
            Column("txn_count", "Number of transactions for the merchant on that day."),
            Column("flagged_txn_count", "Number of flagged-fraud transactions on that day."),
            Column("flagged_rate", "flagged_txn_count / txn_count for that merchant-day."),
        ],
    ),
}


def table_names() -> List[str]:
    """Return the list of known gold table names (without catalog/schema)."""
    return list(GOLD_TABLES.keys())


def build_schema_prompt(catalog: str = DEFAULT_CATALOG) -> str:
    """Render the gold schema into a compact prompt block for an LLM.

    The output is deterministic and intentionally terse so it fits comfortably in
    a system prompt while still giving the model fully-qualified table names and
    column descriptions to ground its SQL generation.

    Args:
        catalog: Catalog name to qualify table identifiers with. Defaults to the
            project default (``txn_intelligence`` unless ``TIL_CATALOG`` is set).

    Returns:
        A multi-line string describing every gold table and its columns.
    """
    lines: List[str] = [
        f"Catalog: {catalog}",
        f"Schema: {GOLD_SCHEMA} (read-only analytics / 'gold' layer)",
        "",
        "Tables (use the fully-qualified names below):",
    ]
    for table in GOLD_TABLES.values():
        lines.append("")
        lines.append(f"- {table.qualified_name(catalog)} -- {table.description}")
        for column in table.columns:
            lines.append(f"    * {column.name}: {column.description}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Allow `python ai/schema_context.py` to print the prompt for inspection.
    print(build_schema_prompt())
