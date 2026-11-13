# Runbook

For whoever looks after the warehouse day to day. Each section stands on its own.

- [What runs, and where to look](#what-runs-and-where-to-look)
- [When an alert fires](#when-an-alert-fires)
- [Re-running and backfilling](#re-running-and-backfilling)
- [Changing a dbt model](#changing-a-dbt-model)
- [Adding a source](#adding-a-source)
- [Access](#access)
- [First deployment](#first-deployment)
- [Costs and switching it off](#costs-and-switching-it-off)

## What runs, and where to look

| Thing | Where |
|---|---|
| Daily schedule | Data Factory, trigger `tr_daily_prod` (06:10 UTC). `tr_daily_dev` exists but is off by default. |
| Orchestration | Data Factory pipeline `pl_daily_prod`: `LoadCrmApi` and `LoadEventFiles` in parallel, then `BuildDbt` |
| API loads | Container Apps job `caj-ingest-prod-<suffix>`, command `python -m ingest api` |
| dbt | Container Apps job `caj-dbt-prod-<suffix>`, `dbt source freshness`, then `dbt build` |
| Load history | `ctl.ingestion_runs` in each database: one row per entity or file per run, with counts and any error |
| Watermarks | `ctl.watermarks`: the newest `updated_at` loaded per API entity |
| Incoming files | Storage account `st<prefix><suffix>`, container `landing`, folder `prod/incoming/event_registrations/` |
| Loaded files | `prod/archive/event_registrations/yyyy/MM/dd/` in the same container |
| Pipeline history | Data Factory Studio, Monitor; or Log Analytics tables `ADFPipelineRun`, `ADFActivityRun` |
| Job logs | Container Apps job, Execution history, then Console logs; or Log Analytics table `ContainerAppConsoleLogs_CL` |
| Alerts | Azure Monitor: `alert-adf-pipeline-failed-*` and `alert-no-successful-daily-run-*`, emailing the action group `ag-data-*` |

The loaders log one JSON object per line, so a query like this finds every error from the jobs:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d)
| where Log_s has '"level": "ERROR"'
| project TimeGenerated, Log_s
```

## When an alert fires

### "A Data Factory pipeline run failed"

1. Open Data Factory Studio, **Monitor**, **Pipeline runs**, and open the failed `pl_daily_prod` run. The failed activity tells you where to look.
2. **`LoadCrmApi` failed.** The ingest job failed.
   - Read its console logs (above) and the latest rows of the run log:
     ```sql
     SELECT TOP 10 entity, status, rows_received, api_retries, error_message, started_at
     FROM ctl.ingestion_runs WHERE source_name = 'crm' ORDER BY started_at DESC;
     ```
   - `ApiError ... returned 401` after a token refresh: the client secret has expired or been rotated. See [rotating the CRM secret](#rotating-the-crm-secret).
   - `still returning 503/429 after N attempts`: the CRM was down or throttling for longer than the retries allow. Usually nothing to fix; re-run later.
   - Database errors mentioning `40613`: the serverless database was waking up and did not come back in time. Re-run.
   - Whatever the cause, nothing is half-loaded. The watermark only moves after the last page, so the next run repeats the work safely.
3. **`LoadEventFiles` failed.** Open the failed `UpsertRegistrations` activity for the error. The file that failed is still in `incoming` and will be retried by the next run.
   - A column error usually means the export's header changed. The loader expects exactly: `registration_id, event_name, event_date, campaign_id, supporter_email, registered_at, ticket_type, fundraising_target_gbp`. Ask the events team to restore it, or change the raw table, the Copy mapping and the staging model together.
4. **`BuildDbt` failed.** The loads worked; dbt stopped on a model error or a failed test.
   - The job's console logs list the failing test by name, for example `not_null_stg_crm__donations_amount_gbp`.
   - Reproduce it against prod read-only, or against dev, with `dbt test --select <test name>`, and look at the rows it returns.
   - **`source freshness` errors** mean a source has not loaded for 50 hours: check the load activities first.
   - A failed test means the marts kept yesterday's data rather than publishing bad data. Fix the cause, then re-run.
5. After fixing, re-run from the failed activity (Monitor, the run, **Rerun from failed activity**).

### "No successful pl_daily_prod run in the last 26 hours"

Nothing failed, so something stopped running. Check, in order: is `tr_daily_prod` started (Manage, Triggers)? Is there a run stuck "In progress" (cancel it and re-run)? Has the Data Factory been stopped or its identity removed?

## Re-running and backfilling

**Re-run a day.** Trigger `pl_daily_prod` manually (Add trigger, Trigger now). Loads are idempotent: re-running changes only what changed in the source.

**Reload an API entity from a date.** Move its watermark back, then run the pipeline. The merge only applies newer versions, so this cannot duplicate or roll back data.

```sql
UPDATE ctl.watermarks SET high_watermark = '2026-10-01T00:00:00Z', updated_at = SYSUTCDATETIME()
WHERE source_name = 'crm' AND entity = 'donations';
```

To reload everything for an entity, delete its row from `ctl.watermarks`.

**Reprocess a file.** Copy it from `archive/.../yyyy/MM/dd/` back to `prod/incoming/event_registrations/` and run the pipeline. Rows upsert on `registration_id`, so reloading is harmless.

**Run one step on its own.**

```bash
az containerapp job start --name caj-ingest-prod-<suffix> --resource-group rg-<prefix>-<suffix>
az containerapp job start --name caj-dbt-prod-<suffix>    --resource-group rg-<prefix>-<suffix>
```

Or use `scripts/ci/run_container_job.sh <job> <resource group>`, which waits for the result.

## Changing a dbt model

1. **Work in your own schema.** Sign in with `az login`, then:
   ```bash
   export SQL_SERVER=sql-<prefix>-<suffix>.database.windows.net SQL_DATABASE=sqldb-warehouse-dev
   export DBT_TARGET=local DBT_USER=<your name> DBT_PROFILES_DIR=$PWD/dbt
   dbt build --project-dir dbt --select +my_model+
   ```
   Your models go to `dbt_<your name>_staging`, `dbt_<your name>_marts` and so on in the dev database. Nobody else's work is touched.
2. **Open a pull request.** Azure DevOps runs the tests, the end-to-end check, and a full `dbt build` against dev in schema `pr_<id>`, then drops it.
3. **Merge.** The release pipeline builds the image, deploys to dev and runs dbt there. When that passes, approve the **warehouse-prod** stage; prod picks up the change at the next run.
4. Clean up your schemas when done: `dbt run-operation drop_schemas_with_prefix --args "{prefix: dbt_<your name>_}"` (it refuses to run against dev or prod targets).

Add tests with the model: at least `unique` and `not_null` on its key. If a new column holds personal data, add `meta: { contains_pii: true }` and keep it out of `marts`: the `assert_no_personal_data_in_marts` test will catch the obvious names, but not everything.

## Adding a source

**A new API entity** (same CRM, for example `/v1/gifts_in_kind`):

1. Add it to `API_ENTITIES` in [`src/ingest/config.py`](../src/ingest/config.py), with its table, key and `updated_at` field.
2. Add a `raw.crm_<name>` table to [`sql/020_raw.sql`](../sql/020_raw.sql) with the same columns as the others. `init-db` creates it; the scripts are safe to re-run.
3. Add it to the `crm` source in [`_sources.yml`](../dbt/models/staging/_sources.yml), then a `stg_crm__<name>` model and tests.

**A new file feed:** a raw table, a delimited-text dataset and an upsert Copy activity in Terraform following `load_event_files`, and a staging model. Agree the column list with whoever produces the file, and write it down.

### Changing Data Factory datasets

Data Factory refuses to delete a dataset that a pipeline still uses. If a change replaces a dataset (a new name or a new resource type), apply the pipeline change first, then the rest:

```bash
bash scripts/azure/update_infra.sh -target=azurerm_data_factory_pipeline.load_event_files
bash scripts/azure/update_infra.sh
```

`update_infra.sh` keeps the images that are already deployed, so it does not rebuild anything.

## Access

| Who | Gets | How |
|---|---|---|
| Data Factory | `SELECT, INSERT, UPDATE` on `raw` | database user for its managed identity (grants script) |
| Jobs identity (one per environment) | `db_ddladmin`, `db_datareader`, `db_datawriter` | grants script |
| Analysts | `SELECT` on `marts` only | role `analytics_reader`; add your Entra group to it |
| Azure DevOps PR builds | as a developer, in the dev database | a database user for the `sc-warehouse-dev` service connection's identity, in `db_ddladmin`, `db_datareader` and `db_datawriter` on dev |
| Administrators | Entra admin on the SQL server | Terraform variable `sql_admin_object_id` (use a group) |

Staging holds email addresses, so only the jobs identity and administrators should read it.

### Rotating the CRM secret

```bash
az containerapp job secret set --name caj-ingest-prod-<suffix> --resource-group rg-<prefix>-<suffix> \
  --secrets crm-client-secret=<new secret>
```

Then update `TF_VAR_crm_client_secret` wherever Terraform runs, so the next `terraform apply` does not put the old one back. Do the same for the dev job.

## First deployment

For a self-contained demonstration with the synthetic CRM, `scripts/azure/deploy.sh` does steps 1 to 5 in one go (see the README). For a real source, in this order:

1. **Prerequisites.** Azure CLI signed in, Terraform 1.6 or later, an Entra group for SQL administrators, and the CRM's API URL and client credentials.
2. **Infrastructure.**
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # fill it in
   export TF_VAR_crm_client_secret='<secret>'
   terraform init && terraform apply
   ```
   For this first apply, set `daily_schedule = { dev = {...}, prod = { hour = 6, minute = 10, enabled = false } }` in `terraform.tfvars`, so nothing runs on a schedule before the real image exists. The jobs start with a placeholder image.
3. **Database users.** For each environment, run the grants script as the SQL Entra admin:
   ```bash
   terraform output -json sql_grants | jq -r .prod > grants_prod.sql
   terraform output -json sql_grants | jq -r .dev  > grants_dev.sql
   ```
   Run each in its database (Query editor in the portal, or `sqlcmd -G`).
4. **Tables.** From your machine, with your IP in `client_ip_addresses` and the ODBC driver installed:
   ```bash
   pip install -r requirements.txt && export PYTHONPATH=src
   SQL_SERVER=<server> SQL_DATABASE=sqldb-warehouse-prod SQL_AUTH=cli python -m ingest init-db
   SQL_SERVER=<server> SQL_DATABASE=sqldb-warehouse-dev  SQL_AUTH=cli python -m ingest init-db
   ```
5. **Image.** `az acr build --registry <acr name> --image warehouse:<git sha> .`, then point the four jobs at it with `az containerapp job update --image ...`. After this the Azure DevOps release pipeline does it on every merge.
6. **Azure DevOps.** Create service connections `sc-warehouse` and `sc-warehouse-dev` (workload identity federation), variable groups `warehouse-deploy` (`RESOURCE_GROUP`, `ACR_NAME`, `ACR_LOGIN_SERVER`, `NAME_SUFFIX`) and `warehouse-dev` (`SQL_SERVER`, `SQL_DATABASE`), and environments `warehouse-dev` and `warehouse-prod` with an **Approvals** check on prod. Add `azure-pipelines/ci.yml` as a build validation policy on `main`, and `azure-pipelines/cd.yml` as the release pipeline.
7. **Prove dev.** Upload a sample export to `dev/incoming/event_registrations/` and trigger `pl_daily_dev` by hand. Check `ctl.ingestion_runs`, the archive folder and the marts.
8. **Go live.** Trigger `pl_daily_prod` by hand once and check the same things. Confirm the alert email arrives by failing a run on purpose in dev (for example, a file with a wrong header). Then set prod's `enabled = true` in `terraform.tfvars` and `terraform apply`: that starts the schedule and switches on the silence alert.

## Costs and switching it off

The databases are serverless: they pause after an hour idle and are billed per second while running. Data Factory bills per activity run, Container Apps jobs per second of execution, and the registry and Log Analytics are small fixed costs. The README has [a monthly estimate](../README.md#what-it-costs), about $20 to $26 at list prices, most of it the prod database's hour of waiting before it pauses. To cut that, lower `auto_pause_delay_in_minutes` in `infra/terraform/main.tf` (15 is the minimum). Set a budget alert on the resource group.

To remove everything: `terraform destroy` in `infra/terraform`. The databases go with it, so export anything you need first.
