variable "databricks_host" {
  description = "Databricks Free Edition workspace URL, e.g. https://dbc-xxxx.cloud.databricks.com. Auth token is read from the DATABRICKS_TOKEN env var (never hardcoded)."
  type        = string
}

variable "catalog_name" {
  description = "Unity Catalog catalog for the project."
  type        = string
  default     = "txn_intelligence"
}

variable "create_catalog" {
  description = "Whether Terraform creates the catalog. Set to false on Databricks Free Edition (Default Storage), where catalogs must be created via the UI; Terraform then manages only the schemas, volumes, and grants inside the existing catalog."
  type        = bool
  default     = true
}

variable "bronze_schema" {
  description = "Bronze (raw ingestion) schema name."
  type        = string
  default     = "bronze"
}

variable "silver_schema" {
  description = "Silver (conformed) schema name."
  type        = string
  default     = "silver"
}

variable "gold_schema" {
  description = "Gold (marts + analytics) schema name."
  type        = string
  default     = "gold"
}

variable "landing_volume" {
  description = "UC Volume in the bronze schema where the generator lands JSON events."
  type        = string
  default     = "landing"
}

variable "checkpoint_volume" {
  description = "UC Volume in the bronze schema for Auto Loader streaming checkpoints."
  type        = string
  default     = "_checkpoints"
}

variable "grant_principal" {
  description = "Principal (group or user) that receives usage/read/write grants."
  type        = string
  default     = "account users"
}

variable "sql_warehouse_id" {
  description = "Existing Free Edition serverless SQL warehouse id to READ (not provision). Leave empty to skip the lookup; set it to expose the JDBC path for dbt."
  type        = string
  default     = ""
}
