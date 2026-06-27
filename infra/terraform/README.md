# Terraform — Unity Catalog provisioning

Provisions the project's Unity Catalog objects on **Databricks Free Edition**:

- 1 catalog (`txn_intelligence` by default)
- 3 schemas: `bronze`, `silver`, `gold`
- 2 managed Volumes in `bronze`: `landing` (generator output) and `_checkpoints` (Auto Loader)
- Grants to a configurable principal (default `account users`)

It does **not** provision a SQL warehouse — Free Edition already supplies a managed
serverless warehouse. Set `sql_warehouse_id` to *read* it and expose its JDBC path.

## Auth (no secrets in code)

```bash
export DATABRICKS_TOKEN="dapi..."          # PowerShell: $env:DATABRICKS_TOKEN="dapi..."
# host is passed as a variable (see terraform.tfvars)
```

## Usage

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # then edit databricks_host
terraform init
terraform fmt -check
terraform validate
terraform plan
terraform apply
```

`terraform.tfvars` and `*.tfstate` are gitignored and must never be committed.
