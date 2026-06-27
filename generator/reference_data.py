"""Generate the customer and merchant reference dimensions.

These are produced once per run and written as JSON to the landing ``reference/`` area.
The transaction generator consumes them in-memory so injected fraud can be anchored to a
customer's home location and spending baseline.
"""

from __future__ import annotations

import json
import os
import random
from typing import Any

from faker import Faker

from .config import (
    HIGH_RISK_CATEGORIES,
    MERCHANT_CATEGORIES,
    RISK_TIERS,
    AppConfig,
)

Customer = dict[str, Any]
Merchant = dict[str, Any]


def _new_faker(seed: int) -> Faker:
    fake = Faker()
    Faker.seed(seed)
    fake.seed_instance(seed)
    return fake


def generate_customers(n: int, seed: int = 42) -> list[Customer]:
    """Create ``n`` customers with a home location and a spend baseline."""

    fake = _new_faker(seed)
    rng = random.Random(seed)
    customers: list[Customer] = []
    for i in range(1, n + 1):
        typical = round(rng.uniform(20.0, 250.0), 2)
        customers.append(
            {
                "customer_id": f"CUST_{i:05d}",
                "name": fake.name(),
                "home_country": fake.country_code(),
                "home_city": fake.city(),
                "home_lat": round(float(fake.latitude()), 6),
                "home_lon": round(float(fake.longitude()), 6),
                "account_open_date": fake.date_between(
                    start_date="-6y", end_date="-30d"
                ).isoformat(),
                "typical_txn_amount": typical,
                # std as a fraction of the mean keeps anomalies meaningful per customer
                "typical_txn_std": round(typical * rng.uniform(0.15, 0.40), 2),
            }
        )
    return customers


def _risk_tier_for(category: str, rng: random.Random) -> str:
    """High-risk categories skew toward higher tiers; others skew low."""

    if category in HIGH_RISK_CATEGORIES:
        return rng.choices(RISK_TIERS, weights=[0.1, 0.3, 0.6])[0]
    return rng.choices(RISK_TIERS, weights=[0.6, 0.3, 0.1])[0]


def generate_merchants(n: int, seed: int = 42) -> list[Merchant]:
    """Create ``n`` merchants spread across categories and risk tiers."""

    fake = _new_faker(seed + 1)  # offset so merchant faker != customer faker
    rng = random.Random(seed + 1)
    merchants: list[Merchant] = []
    for i in range(1, n + 1):
        category = MERCHANT_CATEGORIES[i % len(MERCHANT_CATEGORIES)]
        merchants.append(
            {
                "merchant_id": f"MERCH_{i:05d}",
                "merchant_name": fake.company(),
                "merchant_category": category,
                "merchant_country": fake.country_code(),
                "merchant_city": fake.city(),
                "merchant_lat": round(float(fake.latitude()), 6),
                "merchant_lon": round(float(fake.longitude()), 6),
                "risk_tier": _risk_tier_for(category, rng),
            }
        )
    # Guarantee at least one high-risk merchant so the high_risk pattern is always
    # anchored to a genuinely high-risk_tier merchant (independent of the random draw).
    if merchants and not any(m["risk_tier"] == "high" for m in merchants):
        merchants[0]["risk_tier"] = "high"
    return merchants


def build_reference(config: AppConfig) -> tuple[list[Customer], list[Merchant]]:
    """Generate both dimensions using the seed from config."""

    seed = config.generation.seed
    customers = generate_customers(config.generation.num_customers, seed)
    merchants = generate_merchants(config.generation.num_merchants, seed)
    return customers, merchants


def write_reference_data(
    customers: list[Customer],
    merchants: list[Merchant],
    config: AppConfig,
) -> dict[str, str]:
    """Write the dimensions as JSON arrays to the landing reference directory.

    Returns the written file paths so callers/tests can assert on them.
    """

    ref_dir = config.paths.reference_dir
    os.makedirs(ref_dir, exist_ok=True)
    customers_path = f"{ref_dir}/customers.json"
    merchants_path = f"{ref_dir}/merchants.json"
    with open(customers_path, "w", encoding="utf-8") as fh:
        json.dump(customers, fh, indent=2)
    with open(merchants_path, "w", encoding="utf-8") as fh:
        json.dump(merchants, fh, indent=2)
    return {"customers": customers_path, "merchants": merchants_path}
