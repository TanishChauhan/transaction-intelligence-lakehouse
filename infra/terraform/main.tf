terraform {
  required_version = ">= 1.5.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.50"
    }
  }
}

# Host comes from a variable; the auth token is read from the DATABRICKS_TOKEN
# environment variable by the provider. Never hardcode tokens in this file.
provider "databricks" {
  host = var.databricks_host
}

locals {
  schemas = {
    bronze = var.bronze_schema
    silver = var.silver_schema
    gold   = var.gold_schema
  }

  catalog_name = var.create_catalog ? databricks_catalog.this[0].name : data.databricks_catalog.existing[0].name
}

# --- Catalog ---------------------------------------------------------------- #
# On most accounts Terraform creates the catalog. On Databricks Free Edition
# (Default Storage) catalogs must be created via the UI, so set
# create_catalog=false and reference the pre-existing catalog instead.
resource "databricks_catalog" "this" {
  count   = var.create_catalog ? 1 : 0
  name    = var.catalog_name
  comment = "Transaction Intelligence Lakehouse (Databricks Free Edition)"
}

data "databricks_catalog" "existing" {
  count = var.create_catalog ? 0 : 1
  name  = var.catalog_name
}

# --- Schemas (bronze / silver / gold) --------------------------------------- #
resource "databricks_schema" "layer" {
  for_each     = local.schemas
  catalog_name = local.catalog_name
  name         = each.value
  comment      = "${each.key} layer of the transaction intelligence lakehouse"
}

# --- Volumes (managed; landing zone + streaming checkpoints) ---------------- #
resource "databricks_volume" "landing" {
  name         = var.landing_volume
  catalog_name = local.catalog_name
  schema_name  = databricks_schema.layer["bronze"].name
  volume_type  = "MANAGED"
  comment      = "Landing zone: generator writes JSON transaction events here."
}

resource "databricks_volume" "checkpoints" {
  name         = var.checkpoint_volume
  catalog_name = local.catalog_name
  schema_name  = databricks_schema.layer["bronze"].name
  volume_type  = "MANAGED"
  comment      = "Auto Loader / Structured Streaming checkpoints."
}

# --- Grants ----------------------------------------------------------------- #
resource "databricks_grants" "catalog" {
  catalog = local.catalog_name
  grant {
    principal  = var.grant_principal
    privileges = ["USE_CATALOG"]
  }
}

resource "databricks_grants" "schema" {
  for_each = databricks_schema.layer
  schema   = "${local.catalog_name}.${each.value.name}"
  grant {
    principal  = var.grant_principal
    privileges = ["USE_SCHEMA", "SELECT", "MODIFY", "CREATE_TABLE"]
  }
}

resource "databricks_grants" "landing_volume" {
  volume = databricks_volume.landing.id
  grant {
    principal  = var.grant_principal
    privileges = ["READ_VOLUME", "WRITE_VOLUME"]
  }
}

# --- Read (do NOT provision) the Free Edition serverless SQL warehouse ------- #
# Free Edition supplies a managed serverless warehouse. We only read it so dbt /
# the dashboard can target it. Set var.sql_warehouse_id to enable the lookup.
data "databricks_sql_warehouse" "serverless" {
  count = var.sql_warehouse_id != "" ? 1 : 0
  id    = var.sql_warehouse_id
}
