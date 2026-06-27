output "catalog_name" {
  description = "Provisioned Unity Catalog catalog."
  value       = databricks_catalog.this.name
}

output "schemas" {
  description = "Layer schemas (bronze/silver/gold)."
  value       = { for k, s in databricks_schema.layer : k => s.name }
}

output "landing_volume_path" {
  description = "Volume path the generator writes JSON events to."
  value       = "/Volumes/${var.catalog_name}/${var.bronze_schema}/${var.landing_volume}"
}

output "checkpoint_volume_path" {
  description = "Volume path for Auto Loader checkpoints."
  value       = "/Volumes/${var.catalog_name}/${var.bronze_schema}/${var.checkpoint_volume}"
}

output "sql_warehouse_jdbc_url" {
  description = "JDBC URL of the existing Free Edition warehouse (null unless sql_warehouse_id is set)."
  value       = try(data.databricks_sql_warehouse.serverless[0].jdbc_url, null)
}
