# Handover pack

The checklist for handing the warehouse to the analytics team at the end of the engagement. Nothing is "handed over" until every box is ticked and someone on the team has done the last section themselves.

## 1. What the team now owns

| Thing | Owner after handover | Where it is described |
|---|---|---|
| Daily load and its alerts | Head of Data Analytics (or named deputy) | [Runbook](runbook.md#what-runs-and-where-to-look) |
| dbt models and tests | Analytics team | [Changing a dbt model](runbook.md#changing-a-dbt-model) |
| CRM API credentials | Analytics team, with the CRM administrator | [Rotating the CRM secret](runbook.md#rotating-the-crm-secret) |
| Event export format | Events team, agreed with analytics | [Adding a source](runbook.md#adding-a-source) |
| Azure resources and Terraform state | Analytics team, with IT for subscription access | [First deployment](runbook.md#first-deployment) |

## 2. Access, transferred and checked

- [ ] Analytics team's Entra group is the SQL server's Entra admin (not a personal account)
- [ ] Group added to `analytics_reader` in the prod database
- [ ] Two people can sign in to Data Factory Studio and the Azure portal for the resource group
- [ ] Azure DevOps: the team can approve the `warehouse-prod` environment; at least two approvers
- [ ] Alert action group emails a shared mailbox, not a person
- [ ] Terraform state is in the shared backend, and the team can run `terraform plan` with no changes
- [ ] The engineer's own access has been removed

## 3. Known limits, accepted

Agreed with the Head of Data Analytics, each with a date or a trigger to revisit:

- [ ] Records deleted in the CRM stay in raw (the API reports changes, not deletions)
- [ ] SQL server reachable from Azure services rather than through a private endpoint
- [ ] CRM secret held as a Container Apps secret rather than in Key Vault
- [ ] File order within a single run is not guaranteed if two exports for the same registration wait together

## 4. Recommended improvements

Written up separately with effort and benefit for each: see [decisions.md](decisions.md#worth-revisiting). The first three to consider are a periodic full reconciliation against the CRM (catches deletions), Key Vault for the secret, and a private endpoint for SQL.

## 5. First 30 days: what to watch

- Run time of `pl_daily_prod` in Monitor. It should be stable; a steady rise means a source is growing or a model needs to become incremental.
- `api_retries` in `ctl.ingestion_runs`. A rising count means the CRM is throttling harder; lower the page size or move the schedule.
- Test results in the dbt job logs, especially `warn` results, which do not fail the run.
- The archive folder, to confirm every export arrives once a day.

## 6. Done by the team, not shown

Each of these is done by a member of the analytics team, on their own, before handover is signed off:

- [ ] Re-ran a failed pipeline from the failed activity
- [ ] Moved a watermark back and reloaded a day of donations
- [ ] Made a small model change in their own schema, raised a pull request, merged it and approved the prod stage
- [ ] Rotated the CRM secret in dev

Signed off by: ____________________ Date: ____________
