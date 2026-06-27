"""Load the customer/merchant reference dimensions into bronze.

The generator writes ``customers.json`` / ``merchants.json`` (JSON arrays) to the landing
``reference/`` directory. These are small, slowly-changing dimensions, so a simple batch
read-and-overwrite into managed Delta tables is appropriate (no streaming needed).

Run on Databricks with ``TIL_TARGET=databricks``:

    python -m ingestion.load_reference
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root (parent of the `generator`/`ingestion` packages) is importable
# when this file is run as a bare script (e.g. a Databricks spark_python_task), not just
# via `python -m`. Idempotent and harmless locally.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])  # repo root for `generator.*` imports
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from generator.config import AppConfig, load_config

REFERENCE_TABLES = ("customers", "merchants")


def load_reference(spark: SparkSession, config: AppConfig) -> dict[str, str]:
    """Overwrite bronze.{customers,merchants}_raw from the landing reference JSON arrays."""

    cat, schema = config.paths.catalog, config.paths.bronze_schema
    written: dict[str, str] = {}
    for name in REFERENCE_TABLES:
        table = f"{cat}.{schema}.{name}_raw"
        source_path = f"{config.paths.reference_dir}/{name}.json"
        df = (
            spark.read.option("multiLine", "true")  # files are JSON arrays
            .json(source_path)
            .withColumn("_ingested_at", F.current_timestamp())
            .withColumn("_source_file", F.lit(source_path))
        )
        (
            df.write.format("delta")
            .mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(table)
        )
        written[name] = table
    return written


def main() -> None:
    spark = SparkSession.builder.appName("load_reference").getOrCreate()
    load_reference(spark, load_config())


if __name__ == "__main__":
    main()
