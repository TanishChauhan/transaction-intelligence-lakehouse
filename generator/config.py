"""Central, environment-driven configuration for the synthetic source.

Every tunable (paths, volumes, generation rates, fraud ratios/params) lives here so
nothing downstream hardcodes magic constants. Values can be overridden via environment
variables, which lets the same code run as a local unit test (writing to ``./_landing``)
or as a Databricks job (writing to ``/Volumes/<catalog>/<schema>/<volume>/...``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

# --- Controlled vocabularies (shared contract with dbt accepted_values tests) ---
MERCHANT_CATEGORIES: Final[tuple[str, ...]] = (
    "grocery",
    "fuel",
    "electronics",
    "travel",
    "gambling",
    "crypto",
    "atm",
    "online_marketplace",
)
CHANNELS: Final[tuple[str, ...]] = ("POS", "ECOMMERCE", "ATM")
RISK_TIERS: Final[tuple[str, ...]] = ("low", "medium", "high")
CURRENCIES: Final[tuple[str, ...]] = ("USD", "EUR", "GBP", "INR", "SGD")

# Categories that skew toward a high risk tier.
HIGH_RISK_CATEGORIES: Final[frozenset[str]] = frozenset({"gambling", "crypto"})

# The five injected fraud patterns (also used by tests + downstream docs).
FRAUD_PATTERNS: Final[tuple[str, ...]] = (
    "velocity",
    "impossible_travel",
    "amount_anomaly",
    "high_risk_merchant",
    "card_testing",
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class PathConfig:
    """Resolves landing/reference/checkpoint locations for the active target."""

    target: str  # "local" | "databricks"
    catalog: str
    bronze_schema: str
    landing_volume: str
    checkpoint_volume: str
    local_root: str

    def __post_init__(self) -> None:
        if self.target not in ("local", "databricks"):
            raise ValueError(f"target must be 'local' or 'databricks', got {self.target!r}")

    @property
    def landing_root(self) -> str:
        if self.target == "databricks":
            return f"/Volumes/{self.catalog}/{self.bronze_schema}/{self.landing_volume}"
        return self.local_root.replace("\\", "/")

    @property
    def transactions_dir(self) -> str:
        return f"{self.landing_root}/transactions"

    @property
    def reference_dir(self) -> str:
        return f"{self.landing_root}/reference"

    @property
    def checkpoint_root(self) -> str:
        if self.target == "databricks":
            return f"/Volumes/{self.catalog}/{self.bronze_schema}/{self.checkpoint_volume}"
        return f"{self.local_root.replace(chr(92), '/')}/_checkpoints"


@dataclass(frozen=True)
class GenerationConfig:
    """Reference-data sizes and transaction volume/timeframe."""

    num_customers: int = 200
    num_merchants: int = 40
    num_transactions: int = 5_000  # approximate target (+/- one fraud scenario)
    max_cards_per_customer: int = 2
    days_back: int = 7  # events spread across the trailing N days
    batch_size: int = 500  # events per landing file (Auto Loader reads many files)
    home_jitter_deg: float = 0.4  # +/- degrees applied to home lat/lon for local txns
    # channel sampling weights for (POS, ECOMMERCE, ATM)
    channel_weights: tuple[float, float, float] = (0.55, 0.40, 0.05)
    # Optional fixed ISO8601 UTC anchor for the event window; None => now() (realistic).
    # Set this (e.g. "2026-01-01T00:00:00Z") for reproducible demo/CI runs.
    anchor_time: str | None = None
    seed: int = 42

    def __post_init__(self) -> None:
        for name in (
            "num_customers",
            "num_merchants",
            "num_transactions",
            "max_cards_per_customer",
            "batch_size",
            "days_back",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"GenerationConfig.{name} must be >= 1")


@dataclass(frozen=True)
class FraudConfig:
    """Fraud ratio + per-pattern parameters. Detection never reads these labels."""

    fraud_ratio: float = 0.015  # ~1.5% of events are part of an injected fraud scenario
    pattern_weights: dict[str, float] = field(
        default_factory=lambda: {
            "velocity": 0.30,
            "impossible_travel": 0.20,
            "amount_anomaly": 0.20,
            "high_risk_merchant": 0.15,
            "card_testing": 0.15,
        }
    )
    # velocity: N rapid txns on one card within a short window
    velocity_count: int = 6
    velocity_window_seconds: int = 120
    # impossible travel: two txns geographically distant within a short gap
    impossible_travel_min_km: float = 2_000.0
    impossible_travel_max_gap_seconds: int = 1_800
    # high-risk merchant: a burst of txns at risk_tier='high' merchants ("disproportionate")
    high_risk_burst_count: int = 6
    high_risk_window_seconds: int = 600
    # amount anomaly: amount = typical + (zscore * std)
    amount_anomaly_zscore: float = 8.0
    # card testing: several tiny txns then one large
    card_testing_small_count: int = 5
    card_testing_small_max: float = 2.0
    card_testing_large_min: float = 400.0
    card_testing_step_seconds: int = 15

    def __post_init__(self) -> None:
        if not 0.0 <= self.fraud_ratio <= 1.0:
            raise ValueError("FraudConfig.fraud_ratio must be in [0, 1]")
        for name in ("velocity_count", "high_risk_burst_count", "card_testing_small_count"):
            if getattr(self, name) < 1:
                raise ValueError(f"FraudConfig.{name} must be >= 1")


@dataclass(frozen=True)
class AppConfig:
    paths: PathConfig
    generation: GenerationConfig
    fraud: FraudConfig


def load_config() -> AppConfig:
    """Build the full config from environment variables (with sensible defaults)."""

    paths = PathConfig(
        target=_env("TIL_TARGET", "local"),
        catalog=_env("TIL_CATALOG", "txn_intelligence"),
        bronze_schema=_env("TIL_BRONZE_SCHEMA", "bronze"),
        landing_volume=_env("TIL_LANDING_VOLUME", "landing"),
        checkpoint_volume=_env("TIL_CHECKPOINT_VOLUME", "_checkpoints"),
        local_root=_env("TIL_LOCAL_ROOT", "_landing"),
    )
    anchor = os.environ.get("TIL_ANCHOR_TIME") or None
    generation = GenerationConfig(
        num_customers=_env_int("TIL_NUM_CUSTOMERS", 200),
        num_merchants=_env_int("TIL_NUM_MERCHANTS", 40),
        num_transactions=_env_int("TIL_NUM_TRANSACTIONS", 5_000),
        max_cards_per_customer=_env_int("TIL_MAX_CARDS", 2),
        days_back=_env_int("TIL_DAYS_BACK", 7),
        batch_size=_env_int("TIL_BATCH_SIZE", 500),
        anchor_time=anchor,
        seed=_env_int("TIL_SEED", 42),
    )
    fraud = FraudConfig(
        fraud_ratio=_env_float("TIL_FRAUD_RATIO", 0.015),
        velocity_count=_env_int("TIL_VELOCITY_COUNT", 6),
        velocity_window_seconds=_env_int("TIL_VELOCITY_WINDOW_SECONDS", 120),
        impossible_travel_min_km=_env_float("TIL_IMPOSSIBLE_TRAVEL_MIN_KM", 2_000.0),
        impossible_travel_max_gap_seconds=_env_int("TIL_IMPOSSIBLE_TRAVEL_MAX_GAP_SECONDS", 1_800),
        amount_anomaly_zscore=_env_float("TIL_AMOUNT_ANOMALY_ZSCORE", 8.0),
        high_risk_burst_count=_env_int("TIL_HIGH_RISK_BURST_COUNT", 6),
    )
    return AppConfig(paths=paths, generation=generation, fraud=fraud)
