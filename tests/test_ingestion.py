"""Tests for bronze ingestion (Phase 3).

These require pyspark; they are skipped automatically where Spark is not installed
(e.g. the local dev box) and run in CI / on Databricks where pyspark is available.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from ingestion.bronze_autoloader import (  # noqa: E402  (after importorskip)
    REQUIRED_KEYS,
    build_event_schema,
)

EXPECTED_FIELDS = {
    "transaction_id",
    "event_timestamp",
    "customer_id",
    "card_id",
    "merchant_id",
    "merchant_category",
    "amount",
    "currency",
    "txn_country",
    "txn_city",
    "txn_lat",
    "txn_lon",
    "channel",
    "device_id",
    "is_fraud_label",
}


def test_event_schema_field_names():
    names = {f.name for f in build_event_schema().fields}
    assert names == EXPECTED_FIELDS


def test_event_schema_types():
    types = {f.name: f.dataType.simpleString() for f in build_event_schema().fields}
    assert types["amount"] == "double"
    assert types["txn_lat"] == "double"
    assert types["txn_lon"] == "double"
    assert types["is_fraud_label"] == "boolean"
    assert types["transaction_id"] == "string"


def test_required_keys_are_in_schema():
    names = {f.name for f in build_event_schema().fields}
    assert set(REQUIRED_KEYS).issubset(names)
