"""Unit tests for the synthetic transaction generator (Phase 1).

Covers: reference-dimension schema, event schema/typing, the ECOMMERCE-only device rule,
fraud ratio tolerance, impossible-travel distance, haversine correctness, and local
landing IO. No Spark/Databricks required.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import uuid
from dataclasses import replace

import pytest

from generator import generate_transactions as gt
from generator import reference_data as rd
from generator.config import (
    CHANNELS,
    CURRENCIES,
    MERCHANT_CATEGORIES,
    RISK_TIERS,
    AppConfig,
    load_config,
)

EVENT_KEYS = {
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


def make_config(tmp_path, total: int = 2_000, ratio: float = 0.05) -> AppConfig:
    base = load_config()
    paths = replace(base.paths, target="local", local_root=str(tmp_path))
    gen = replace(
        base.generation,
        num_customers=80,
        num_merchants=20,
        num_transactions=total,
        seed=123,
    )
    fraud = replace(base.fraud, fraud_ratio=ratio)
    return AppConfig(paths=paths, generation=gen, fraud=fraud)


# --------------------------------------------------------------------------- #
# Reference dimensions
# --------------------------------------------------------------------------- #
def test_customers_schema():
    customers = rd.generate_customers(25, seed=7)
    assert len(customers) == 25
    keys = {
        "customer_id",
        "name",
        "home_country",
        "home_city",
        "home_lat",
        "home_lon",
        "account_open_date",
        "typical_txn_amount",
        "typical_txn_std",
    }
    for c in customers:
        assert keys == set(c)
        assert c["customer_id"].startswith("CUST_")
        assert -90 <= c["home_lat"] <= 90
        assert -180 <= c["home_lon"] <= 180
        assert c["typical_txn_amount"] > 0
        assert c["typical_txn_std"] > 0


def test_merchants_schema_and_risk_tiers():
    merchants = rd.generate_merchants(40, seed=7)
    assert len(merchants) == 40
    for m in merchants:
        assert m["merchant_id"].startswith("MERCH_")
        assert m["merchant_category"] in MERCHANT_CATEGORIES
        assert m["risk_tier"] in RISK_TIERS


def test_reference_is_deterministic():
    assert rd.generate_customers(10, seed=99) == rd.generate_customers(10, seed=99)


# --------------------------------------------------------------------------- #
# Event schema & typing
# --------------------------------------------------------------------------- #
def test_event_schema_and_types(tmp_path):
    config = make_config(tmp_path)
    customers, merchants = rd.build_reference(config)
    events = gt.generate_events(config, customers, merchants)
    assert events, "expected events to be generated"
    for e in events:
        assert EVENT_KEYS == set(e)
        assert isinstance(e["amount"], float) and e["amount"] > 0
        assert isinstance(e["txn_lat"], float) and isinstance(e["txn_lon"], float)
        assert isinstance(e["is_fraud_label"], bool)
        assert e["channel"] in CHANNELS
        assert e["merchant_category"] in MERCHANT_CATEGORIES
        assert e["currency"] in CURRENCIES
        # device_id is populated only for the ECOMMERCE channel
        if e["channel"] == "ECOMMERCE":
            assert isinstance(e["device_id"], str)
        else:
            assert e["device_id"] is None


def test_events_sorted_by_timestamp(tmp_path):
    config = make_config(tmp_path)
    customers, merchants = rd.build_reference(config)
    events = gt.generate_events(config, customers, merchants)
    timestamps = [e["event_timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


# --------------------------------------------------------------------------- #
# Fraud injection
# --------------------------------------------------------------------------- #
def test_fraud_ratio_within_tolerance(tmp_path):
    ratio = 0.05
    config = make_config(tmp_path, total=4_000, ratio=ratio)
    customers, merchants = rd.build_reference(config)
    events = gt.generate_events(config, customers, merchants)
    observed = sum(1 for e in events if e["is_fraud_label"]) / len(events)
    assert abs(observed - ratio) <= 0.02, f"fraud ratio {observed:.4f} off target {ratio}"


def test_impossible_travel_pairs_are_distant(tmp_path):
    config = make_config(tmp_path)
    customer = rd.generate_customers(1, seed=5)[0]
    merchant = rd.generate_merchants(1, seed=5)[0]
    rng = random.Random(1)
    import datetime as _dt

    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    for _ in range(20):  # repeat: must hold every time, not by luck
        pair = gt.inject_impossible_travel(customer, merchant, "CARD_X", base, config, rng)
        assert len(pair) == 2
        dist = gt.haversine_km(
            pair[0]["txn_lat"], pair[0]["txn_lon"], pair[1]["txn_lat"], pair[1]["txn_lon"]
        )
        assert dist >= config.fraud.impossible_travel_min_km


def test_card_testing_shape(tmp_path):
    config = make_config(tmp_path)
    customer = rd.generate_customers(1, seed=5)[0]
    merchant = rd.generate_merchants(1, seed=5)[0]
    import datetime as _dt

    base = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    events = gt.inject_card_testing(customer, merchant, "CARD_X", base, config, random.Random(1))
    smalls = events[:-1]
    big = events[-1]
    assert all(e["amount"] <= config.fraud.card_testing_small_max for e in smalls)
    assert big["amount"] >= config.fraud.card_testing_large_min
    assert all(e["channel"] == "ECOMMERCE" for e in events)


# --------------------------------------------------------------------------- #
# Geo helper
# --------------------------------------------------------------------------- #
def test_haversine_known_distance():
    # London -> Paris is ~343 km
    london = (51.5074, -0.1278)
    paris = (48.8566, 2.3522)
    dist = gt.haversine_km(*london, *paris)
    assert 310 <= dist <= 360


# --------------------------------------------------------------------------- #
# Local landing IO
# --------------------------------------------------------------------------- #
def test_generate_and_write_local(tmp_path):
    config = make_config(tmp_path, total=1_000)
    summary = gt.generate_and_write(config)

    assert summary["target"] == "local"
    assert summary["events"] >= 1_000
    assert summary["transaction_files"] >= 1

    txn_dir = tmp_path / "transactions"
    files = list(txn_dir.glob("txns_*.json"))
    assert files, "no transaction files written"

    # Reference dimensions persisted
    assert (tmp_path / "reference" / "customers.json").exists()
    assert (tmp_path / "reference" / "merchants.json").exists()

    # Every line is a valid JSON event with the expected schema
    with open(files[0], encoding="utf-8") as fh:
        first = json.loads(fh.readline())
    assert EVENT_KEYS == set(first)


# --------------------------------------------------------------------------- #
# Per-injector behaviour (added after Tester review)
# --------------------------------------------------------------------------- #
_BASE_TS = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _one_customer_merchant(seed: int = 5):
    return rd.generate_customers(1, seed=seed)[0], rd.generate_merchants(1, seed=seed)[0]


def test_velocity_injector(tmp_path):
    config = make_config(tmp_path)
    customer, merchant = _one_customer_merchant()
    evs = gt.inject_velocity(customer, merchant, "CARD_X", _BASE_TS, config, random.Random(1))
    assert len(evs) == config.fraud.velocity_count
    assert all(e["is_fraud_label"] is True for e in evs)
    assert all(e["card_id"] == "CARD_X" for e in evs)
    span = (
        dt.datetime.fromisoformat(evs[-1]["event_timestamp"].replace("Z", "+00:00"))
        - dt.datetime.fromisoformat(evs[0]["event_timestamp"].replace("Z", "+00:00"))
    ).total_seconds()
    assert span <= config.fraud.velocity_window_seconds


def test_amount_anomaly_formula(tmp_path):
    config = make_config(tmp_path)
    customer, merchant = _one_customer_merchant()
    ev = gt.inject_amount_anomaly(customer, merchant, "C", _BASE_TS, config, random.Random(1))[0]
    expected = (
        customer["typical_txn_amount"]
        + config.fraud.amount_anomaly_zscore * customer["typical_txn_std"]
    )
    assert ev["amount"] == round(expected, 2)
    assert ev["is_fraud_label"] is True


def test_high_risk_merchant_burst(tmp_path):
    config = make_config(tmp_path)
    customer, _ = _one_customer_merchant()
    high = {
        "merchant_id": "MERCH_H",
        "merchant_name": "Risky Co",
        "merchant_category": "gambling",
        "merchant_country": "US",
        "merchant_city": "Vegas",
        "merchant_lat": 36.1,
        "merchant_lon": -115.1,
        "risk_tier": "high",
    }
    evs = gt.inject_high_risk_merchant(customer, high, "C", _BASE_TS, config, random.Random(1))
    assert len(evs) == config.fraud.high_risk_burst_count
    assert all(e["is_fraud_label"] is True for e in evs)
    assert all(e["merchant_id"] == "MERCH_H" for e in evs)


def test_zero_fraud_ratio_yields_only_normals(tmp_path):
    config = make_config(tmp_path, total=500, ratio=0.0)
    customers, merchants = rd.build_reference(config)
    events = gt.generate_events(config, customers, merchants)
    assert events
    assert all(e["is_fraud_label"] is False for e in events)


def test_event_timestamp_and_id_formats(tmp_path):
    config = make_config(tmp_path, total=300)
    customers, merchants = rd.build_reference(config)
    events = gt.generate_events(config, customers, merchants)
    for e in events[:50]:
        assert e["event_timestamp"].endswith("Z")
        dt.datetime.fromisoformat(e["event_timestamp"].replace("Z", "+00:00"))  # parses
        uuid.UUID(e["transaction_id"])  # valid UUID


# --------------------------------------------------------------------------- #
# Config: path resolution, validation, reproducibility
# --------------------------------------------------------------------------- #
def test_databricks_path_resolution():
    base = load_config()
    paths = replace(
        base.paths,
        target="databricks",
        catalog="cat",
        bronze_schema="bronze",
        landing_volume="landing",
        checkpoint_volume="_checkpoints",
    )
    assert paths.landing_root == "/Volumes/cat/bronze/landing"
    assert paths.transactions_dir == "/Volumes/cat/bronze/landing/transactions"
    assert paths.reference_dir == "/Volumes/cat/bronze/landing/reference"
    assert paths.checkpoint_root == "/Volumes/cat/bronze/_checkpoints"


def test_config_validation_rejects_bad_sizes():
    base = load_config()
    with pytest.raises(ValueError):
        replace(base.generation, num_customers=0)
    with pytest.raises(ValueError):
        replace(base.fraud, velocity_count=0)
    with pytest.raises(ValueError):
        replace(base.fraud, fraud_ratio=1.5)


def test_anchor_time_makes_timestamps_reproducible(tmp_path):
    base = load_config()
    paths = replace(base.paths, target="local", local_root=str(tmp_path))
    gen = replace(
        base.generation,
        num_customers=40,
        num_merchants=10,
        num_transactions=300,
        seed=7,
        anchor_time="2026-01-01T00:00:00Z",
    )
    fraud = replace(base.fraud, fraud_ratio=0.05)
    cfg = AppConfig(paths=paths, generation=gen, fraud=fraud)
    customers, merchants = rd.build_reference(cfg)
    run1 = [e["event_timestamp"] for e in gt.generate_events(cfg, customers, merchants)]
    run2 = [e["event_timestamp"] for e in gt.generate_events(cfg, customers, merchants)]
    assert run1 == run2
