"""Synthetic card-transaction event producer.

Emits newline-delimited JSON transaction events to the landing area (local dir or UC
Volume). A configurable fraction of events belong to one of five **injected** fraud
scenarios; those events carry ``is_fraud_label = True`` as ground truth for *validation
only*. The downstream pipeline must derive its own signals and never read this label.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the repo root (parent of the `generator`/`ingestion` packages) is importable
# when this file is run as a bare script, not just via `python -m`. A Databricks
# spark_python_task execs the file WITHOUT defining `__file__`, so fall back to the
# current code object's filename, which is always set. Idempotent and harmless locally.
try:
    _self_path = Path(__file__)
except NameError:  # e.g. Databricks spark_python_task exec context
    _self_path = Path(sys._getframe().f_code.co_filename)
_REPO_ROOT = str(_self_path.resolve().parents[1])  # repo root for `generator.*`/`ingestion.*` imports
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import json
import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from generator.config import CHANNELS, CURRENCIES, AppConfig, GenerationConfig, load_config
from generator.reference_data import (
    Customer,
    Merchant,
    build_reference,
    write_reference_data,
)

Event = dict[str, Any]

_EARTH_RADIUS_KM = 6371.0


# --------------------------------------------------------------------------- #
# Geo helpers
# --------------------------------------------------------------------------- #
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""

    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _distant_point(
    lat: float, lon: float, min_km: float, rng: random.Random
) -> tuple[float, float]:
    """Return a random point at least ``min_km`` away from (lat, lon)."""

    for _ in range(100):
        tlat = rng.uniform(-90.0, 90.0)
        tlon = rng.uniform(-180.0, 180.0)
        if haversine_km(lat, lon, tlat, tlon) >= min_km:
            return round(tlat, 6), round(tlon, 6)
    # Fallback: antipode is always maximally distant.
    return round(-lat, 6), round((lon + 180.0) % 360.0 - 180.0, 6)


# --------------------------------------------------------------------------- #
# Event construction
# --------------------------------------------------------------------------- #
def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def assign_cards(
    customers: list[Customer], max_cards: int, rng: random.Random
) -> dict[str, list[str]]:
    """Assign 1..max_cards card ids per customer (deterministic given the rng)."""

    cards: dict[str, list[str]] = {}
    for c in customers:
        n = rng.randint(1, max(1, max_cards))
        suffix = c["customer_id"].split("_")[-1]
        cards[c["customer_id"]] = [f"CARD_{suffix}_{k}" for k in range(1, n + 1)]
    return cards


def _channel(
    category: str,
    rng: random.Random,
    weights: tuple[float, float, float],
    force: str | None = None,
) -> str:
    """Pick a channel: forced if given, ATM for atm merchants, else weighted sample."""

    if force:
        return force
    if category == "atm":
        return "ATM"
    return rng.choices(CHANNELS, weights=list(weights))[0]


def make_event(
    customer: Customer,
    merchant: Merchant,
    card_id: str,
    ts: datetime,
    rng: random.Random,
    *,
    gen: GenerationConfig | None = None,
    amount: float | None = None,
    is_fraud: bool = False,
    location: tuple[float, float, str, str] | None = None,
    channel: str | None = None,
) -> Event:
    """Build a single transaction event. Location defaults to near the customer's home.

    Channel weights and home jitter are read from ``gen`` when provided so behaviour is
    fully config-driven; sensible defaults apply otherwise.
    """

    weights = gen.channel_weights if gen is not None else (0.55, 0.40, 0.05)
    jitter = gen.home_jitter_deg if gen is not None else 0.4
    category = merchant["merchant_category"]
    ch = _channel(category, rng, weights, force=channel)

    if location is None:
        lat = round(customer["home_lat"] + rng.uniform(-jitter, jitter), 6)
        lon = round(customer["home_lon"] + rng.uniform(-jitter, jitter), 6)
        country, city = customer["home_country"], customer["home_city"]
    else:
        lat, lon, country, city = location

    if amount is None:
        amount = max(0.50, rng.gauss(customer["typical_txn_amount"], customer["typical_txn_std"]))

    return {
        "transaction_id": str(uuid.uuid4()),
        "event_timestamp": _iso(ts),
        "customer_id": customer["customer_id"],
        "card_id": card_id,
        "merchant_id": merchant["merchant_id"],
        "merchant_category": category,
        "amount": round(float(amount), 2),
        "currency": rng.choice(CURRENCIES),
        "txn_country": country,
        "txn_city": city,
        "txn_lat": lat,
        "txn_lon": lon,
        "channel": ch,
        "device_id": (f"DEV_{uuid.uuid4().hex[:12]}" if ch == "ECOMMERCE" else None),
        "is_fraud_label": is_fraud,
    }


# --------------------------------------------------------------------------- #
# Injected fraud patterns (each returns a list of labelled events)
# --------------------------------------------------------------------------- #
def inject_velocity(
    customer: Customer,
    merchant: Merchant,
    card_id: str,
    base_ts: datetime,
    config: AppConfig,
    rng: random.Random,
) -> list[Event]:
    """N rapid transactions on one card inside a short window."""

    fc = config.fraud
    step = max(1, fc.velocity_window_seconds // fc.velocity_count)
    return [
        make_event(
            customer,
            merchant,
            card_id,
            base_ts + timedelta(seconds=i * step),
            rng,
            gen=config.generation,
            is_fraud=True,
        )
        for i in range(fc.velocity_count)
    ]


def inject_impossible_travel(
    customer: Customer,
    merchant: Merchant,
    card_id: str,
    base_ts: datetime,
    config: AppConfig,
    rng: random.Random,
) -> list[Event]:
    """Two transactions geographically distant within an implausibly short gap."""

    fc = config.fraud
    home_loc = (
        round(customer["home_lat"], 6),
        round(customer["home_lon"], 6),
        customer["home_country"],
        customer["home_city"],
    )
    dlat, dlon = _distant_point(
        customer["home_lat"], customer["home_lon"], fc.impossible_travel_min_km, rng
    )
    far_loc = (dlat, dlon, merchant["merchant_country"], merchant["merchant_city"])
    gap = rng.randint(60, fc.impossible_travel_max_gap_seconds)
    return [
        make_event(
            customer,
            merchant,
            card_id,
            base_ts,
            rng,
            gen=config.generation,
            is_fraud=True,
            location=home_loc,
        ),
        make_event(
            customer,
            merchant,
            card_id,
            base_ts + timedelta(seconds=gap),
            rng,
            gen=config.generation,
            is_fraud=True,
            location=far_loc,
        ),
    ]


def inject_amount_anomaly(
    customer: Customer,
    merchant: Merchant,
    card_id: str,
    base_ts: datetime,
    config: AppConfig,
    rng: random.Random,
) -> list[Event]:
    """A single transaction far above the customer's spend baseline."""

    amount = (
        customer["typical_txn_amount"]
        + config.fraud.amount_anomaly_zscore * customer["typical_txn_std"]
    )
    return [
        make_event(
            customer,
            merchant,
            card_id,
            base_ts,
            rng,
            gen=config.generation,
            amount=amount,
            is_fraud=True,
        )
    ]


def inject_high_risk_merchant(
    customer: Customer,
    merchant: Merchant,
    card_id: str,
    base_ts: datetime,
    config: AppConfig,
    rng: random.Random,
) -> list[Event]:
    """A disproportionate burst of transactions at a high-risk merchant.

    The caller selects a ``risk_tier == 'high'`` merchant; this emits several txns on the
    same card within a short window so merchant-risk-rate rules have a signal to catch.
    """

    fc = config.fraud
    step = max(1, fc.high_risk_window_seconds // fc.high_risk_burst_count)
    return [
        make_event(
            customer,
            merchant,
            card_id,
            base_ts + timedelta(seconds=i * step),
            rng,
            gen=config.generation,
            is_fraud=True,
        )
        for i in range(fc.high_risk_burst_count)
    ]


def inject_card_testing(
    customer: Customer,
    merchant: Merchant,
    card_id: str,
    base_ts: datetime,
    config: AppConfig,
    rng: random.Random,
) -> list[Event]:
    """Several tiny transactions followed by a large one (card-testing pattern)."""

    fc = config.fraud
    step = fc.card_testing_step_seconds
    events: list[Event] = []
    for i in range(fc.card_testing_small_count):
        events.append(
            make_event(
                customer,
                merchant,
                card_id,
                base_ts + timedelta(seconds=i * step),
                rng,
                gen=config.generation,
                amount=round(rng.uniform(0.5, fc.card_testing_small_max), 2),
                is_fraud=True,
                channel="ECOMMERCE",
            )
        )
    events.append(
        make_event(
            customer,
            merchant,
            card_id,
            base_ts + timedelta(seconds=fc.card_testing_small_count * step + 2 * step),
            rng,
            gen=config.generation,
            amount=round(fc.card_testing_large_min + rng.uniform(0, 300), 2),
            is_fraud=True,
            channel="ECOMMERCE",
        )
    )
    return events


_PATTERN_FUNCS: dict[str, Callable[..., list[Event]]] = {
    "velocity": inject_velocity,
    "impossible_travel": inject_impossible_travel,
    "amount_anomaly": inject_amount_anomaly,
    "high_risk_merchant": inject_high_risk_merchant,
    "card_testing": inject_card_testing,
}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def generate_events(
    config: AppConfig, customers: list[Customer], merchants: list[Merchant]
) -> list[Event]:
    """Produce a mix of normal and injected-fraud events, sorted by timestamp."""

    if not customers or not merchants:
        raise ValueError("generate_events requires at least one customer and one merchant")

    rng = random.Random(config.generation.seed + 7)
    cards = assign_cards(customers, config.generation.max_cards_per_customer, rng)
    high_risk = [m for m in merchants if m["risk_tier"] == "high"] or merchants

    # Window ends at the anchor (default: now, for a realistic near-real-time stream).
    if config.generation.anchor_time:
        end = datetime.fromisoformat(config.generation.anchor_time.replace("Z", "+00:00"))
    else:
        end = datetime.now(timezone.utc)
    window_seconds = config.generation.days_back * 24 * 3600
    start = end - timedelta(seconds=window_seconds)

    def random_ts() -> datetime:
        return start + timedelta(seconds=rng.randint(0, window_seconds))

    total = config.generation.num_transactions
    target_fraud = round(total * config.fraud.fraud_ratio)
    patterns = list(config.fraud.pattern_weights.keys())
    weights = list(config.fraud.pattern_weights.values())

    events: list[Event] = []
    fraud_events = 0
    while fraud_events < target_fraud:
        pattern = rng.choices(patterns, weights=weights)[0]
        customer = rng.choice(customers)
        card = rng.choice(cards[customer["customer_id"]])
        merchant = rng.choice(high_risk if pattern == "high_risk_merchant" else merchants)
        produced = _PATTERN_FUNCS[pattern](customer, merchant, card, random_ts(), config, rng)
        events.extend(produced)
        fraud_events += len(produced)

    for _ in range(max(0, total - len(events))):
        customer = rng.choice(customers)
        card = rng.choice(cards[customer["customer_id"]])
        merchant = rng.choice(merchants)
        events.append(make_event(customer, merchant, card, random_ts(), rng, gen=config.generation))

    events.sort(key=lambda e: e["event_timestamp"])
    return events


def write_events(events: list[Event], config: AppConfig) -> list[str]:
    """Write events as batched JSONL files into the landing transactions directory."""

    txn_dir = config.paths.transactions_dir
    os.makedirs(txn_dir, exist_ok=True)
    batch_size = config.generation.batch_size
    paths: list[str] = []
    for batch_idx, start in enumerate(range(0, len(events), batch_size)):
        chunk = events[start : start + batch_size]
        path = f"{txn_dir}/txns_{batch_idx:04d}_{uuid.uuid4().hex[:8]}.json"
        with open(path, "w", encoding="utf-8") as fh:
            for event in chunk:
                fh.write(json.dumps(event) + "\n")
        paths.append(path)
    return paths


def generate_and_write(config: AppConfig) -> dict[str, Any]:
    """End-to-end: build + persist reference data and transaction events."""

    customers, merchants = build_reference(config)
    ref_paths = write_reference_data(customers, merchants, config)
    events = generate_events(config, customers, merchants)
    files = write_events(events, config)
    fraud_count = sum(1 for e in events if e["is_fraud_label"])
    return {
        "target": config.paths.target,
        "landing_root": config.paths.landing_root,
        "customers": len(customers),
        "merchants": len(merchants),
        "events": len(events),
        "fraud_events": fraud_count,
        "fraud_ratio": round(fraud_count / len(events), 4) if events else 0.0,
        "transaction_files": len(files),
        "reference_files": ref_paths,
    }


def main() -> None:
    config = load_config()
    summary = generate_and_write(config)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
