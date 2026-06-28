"""Bronze ingestion via Spark Structured Streaming + Auto Loader.

Reads the JSON transaction events the generator lands on a Unity Catalog Volume and
appends them to the ``bronze.transactions_raw`` managed Delta table, preserving the raw
payload with minimal transformation (schema-on-read). Malformed / schema-mismatched
records are routed to ``bronze.transactions_quarantine`` instead of being dropped.

Free Edition constraints honoured:
- Auto Loader in **directory-listing** mode (no file-notification cloud resources).
- ``trigger(availableNow=True)`` micro-batch-then-stop (never an always-on stream).
- All paths are UC Volumes (``/Volumes/...``); checkpoints live on a Volume.

Run on Databricks (serverless) with ``TIL_TARGET=databricks`` set so config resolves
``/Volumes/...`` paths:

    python -m ingestion.bronze_autoloader
"""

from __future__ import annotations

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

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    StringType,
    StructField,
    StructType,
)

from generator.config import AppConfig, load_config

RESCUED_COLUMN = "_rescued_data"
# Keys that must be present for a record to be considered structurally valid.
REQUIRED_KEYS = ("transaction_id", "event_timestamp", "customer_id")


def build_event_schema() -> StructType:
    """Explicit schema for the transaction event (schema-on-read for bronze).

    Timestamps stay as strings here; type-casting/conformance happens in silver (dbt).
    """

    return StructType(
        [
            StructField("transaction_id", StringType()),
            StructField("event_timestamp", StringType()),
            StructField("customer_id", StringType()),
            StructField("card_id", StringType()),
            StructField("merchant_id", StringType()),
            StructField("merchant_category", StringType()),
            StructField("amount", DoubleType()),
            StructField("currency", StringType()),
            StructField("txn_country", StringType()),
            StructField("txn_city", StringType()),
            StructField("txn_lat", DoubleType()),
            StructField("txn_lon", DoubleType()),
            StructField("channel", StringType()),
            StructField("device_id", StringType()),
            StructField("is_fraud_label", BooleanType()),
        ]
    )


def _table_names(config: AppConfig) -> tuple[str, str]:
    cat, schema = config.paths.catalog, config.paths.bronze_schema
    return f"{cat}.{schema}.transactions_raw", f"{cat}.{schema}.transactions_quarantine"


def read_landing_stream(spark: SparkSession, config: AppConfig) -> DataFrame:
    """Create the Auto Loader streaming DataFrame over the landing transactions dir."""

    schema_location = f"{config.paths.checkpoint_root}/bronze_schema"
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.useNotifications", "false")  # directory-listing mode
        .option("cloudFiles.schemaLocation", schema_location)
        .option("cloudFiles.rescuedDataColumn", RESCUED_COLUMN)
        .schema(build_event_schema())
        .load(config.paths.transactions_dir)
        # ingestion metadata: source file path is exposed via the hidden _metadata column
        .withColumn("_source_file", F.col("_metadata.file_path"))
    )


def split_valid_quarantine(microbatch: DataFrame, batch_id: int) -> tuple[DataFrame, DataFrame]:
    """Tag each row valid/invalid and return (valid_df, quarantine_df)."""

    valid_predicate = F.col(RESCUED_COLUMN).isNull()
    for key in REQUIRED_KEYS:
        valid_predicate = valid_predicate & F.col(key).isNotNull()

    enriched = (
        microbatch.withColumn("_ingested_at", F.current_timestamp())
        .withColumn("_batch_id", F.lit(batch_id))
        .withColumn("_is_valid", valid_predicate)
    )

    valid = enriched.filter(F.col("_is_valid")).drop("_is_valid", RESCUED_COLUMN)
    quarantine = (
        enriched.filter(~F.col("_is_valid"))
        .drop("_is_valid")
        .withColumn("_quarantine_reason", F.lit("schema_mismatch_or_null_required_key"))
    )
    return valid, quarantine


def run_bronze_ingest(spark: SparkSession, config: AppConfig) -> None:
    """Run one availableNow micro-batch pass: landing JSON -> bronze (+ quarantine)."""

    bronze_table, quarantine_table = _table_names(config)
    checkpoint = f"{config.paths.checkpoint_root}/bronze_transactions_raw"

    def process_batch(microbatch: DataFrame, batch_id: int) -> None:
        valid, quarantine = split_valid_quarantine(microbatch, batch_id)
        (
            valid.write.format("delta")
            .mode("append")
            .option("mergeSchema", "true")
            .saveAsTable(bronze_table)
        )
        if not quarantine.isEmpty():
            (
                quarantine.write.format("delta")
                .mode("append")
                .option("mergeSchema", "true")
                .saveAsTable(quarantine_table)
            )

    query = (
        read_landing_stream(spark, config)
        .writeStream.foreachBatch(process_batch)
        .option("checkpointLocation", checkpoint)
        .trigger(availableNow=True)
        .start()
    )
    query.awaitTermination()


def main() -> None:
    spark = SparkSession.builder.appName("bronze_autoloader").getOrCreate()
    run_bronze_ingest(spark, load_config())


if __name__ == "__main__":
    main()
