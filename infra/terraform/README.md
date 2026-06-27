# Terraform — Unity Catalog provisioning

Provisions the project's Unity Catalog objects on **Databricks Free Edition**:

- 1 catalog (`txn_intelligence` by default)
- 3 schemas: `bronze`, `silver`, `gold`
- 2 managed Volumes in `bronze`: `landing` (generator output) and `_checkpoints` (Auto Loader)
- Grants to a configurable principal (default `account users`)

It does **not** provision a SQL warehouse — Free Edition already supplies a managed
serverless warehouse. Set `sql_warehouse_id` to *read* it and expose its JDBC path.

## Catalog creation on Free Edition (Default Storage)

On **Databricks Free Edition with Default Storage**, catalogs **cannot** be created
via the API/Terraform — they must be created in the UI. The `create_catalog`
variable controls this:

- `create_catalog = true` (default): Terraform **creates** the catalog. Use this on
  accounts that allow catalog creation via the API.
- `create_catalog = false`: Terraform **references** a pre-existing catalog (looked
  up by `catalog_name`) and manages only the schemas, volumes, and grants inside it.

### Free Edition steps

1. In the workspace, open **Catalog Explorer → Create catalog → Default storage** and
   create the catalog (default name `txn_intelligence`, matching `catalog_name`).
2. Run Terraform with `create_catalog=false`:

```bash
terraform apply -var="databricks_host=..." -var="create_catalog=false" -var="sql_warehouse_id=..."
```

On accounts that allow API catalog creation, leave the default `create_catalog=true`.

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
