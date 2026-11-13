# Decisions

Each choice, why it was made, what it costs, and what would make it worth changing.

| # | Decision | Why | Trade-off | Revisit when |
|---|---|---|---|---|
| 1 | API records land in `raw` as the JSON the API returned | A new or renamed field never breaks a load; the original record is always there to check against | Typing happens in dbt staging, so a malformed value appears as a failed test rather than a failed load | Raw volumes make JSON parsing in views slow (materialise staging as tables) |
| 2 | Incremental loads re-read a 30-minute overlap behind the high watermark | Source systems commit some rows late with an earlier timestamp; without the overlap those rows are silently skipped (shown by `test_without_an_overlap_window_late_commits_are_lost`) | A few rows are fetched twice per run | The CRM documents a different commit lag; set `INGEST_LOOKBACK_MINUTES` to match |
| 3 | `MERGE` applies a record only if its `updated_at` is strictly newer | Replays and overlaps are harmless, and an out-of-order page cannot roll a row back | Relies on the source maintaining `updated_at` on every change | The source turns out to update rows without touching `updated_at` |
| 4 | The watermark moves only after the last page is merged | A failure mid-run is repeated in full next time, never left half-applied | A run that fails late repeats its earlier pages | Runs get long enough that resuming from the last page matters |
| 5 | The API loader is Python in a Container Apps job, started by Data Factory | Data Factory's REST connector handles simple pagination, but not this API's combination of OAuth token refresh, cursor paging, `Retry-After` and a lookback window, and it has no native way to run dbt. One job pattern runs both, while scheduling, dependencies and alerts stay in Data Factory | Two technologies instead of one | A future source is simple enough for a plain Copy activity (use one) |
| 6 | Files are upserted by Data Factory's Copy activity and archived only after success | Native, no code to maintain, idempotent on `registration_id`; a failed file stays put for the next run | Copy's upsert rewrites matched rows whether or not they changed (the Python loader, used in CI and for backfills, skips unchanged rows) | Volumes make unnecessary rewrites expensive |
| 7 | dev and prod are separate databases; developers and CI use prefixed schemas in dev | Development cannot overwrite production, and permissions differ cleanly per environment | Dev has to be loaded separately | Never, for a team of this size |
| 8 | Pull requests run `dbt build` against the dev database in a throwaway schema | Tests run on real data shapes before merge, not just on synthetic CI data | Needs a database user for the build identity in dev | |
| 9 | One image, built once, promoted from dev to prod with an approval | Prod runs exactly what dev proved | Prod waits for a person | |
| 10 | Managed identities everywhere; SQL logins disabled | No passwords to rotate or leak for Azure-to-Azure access | First deployment needs a grants script run by an admin | |
| 11 | Two alerts: any failed run, and no successful prod run for 26 hours | A switched-off trigger or a stuck run fails nothing, so failure alerts alone miss it | Two alerts to own | |
| 12 | Email addresses stop at staging; marts carry a CRM ID only, enforced by a test | Analysts get what they need for reporting without personal contact data | Matching across sources needs the hash in staging, which is itself personal data and is access-controlled | A use case genuinely needs contact data in reporting (handle it as a separate, restricted mart) |

## Worth revisiting

In rough order of value for effort:

1. **Periodic full reconciliation with the CRM.** Weekly, compare the set of IDs in the source with raw and flag deletions. Today a record deleted at source stays in the warehouse.
2. **Key Vault for the CRM secret**, referenced by the Container Apps job, instead of a secret set from Terraform.
3. **Private endpoint for SQL** and a managed virtual network for Data Factory, then remove the "allow Azure services" firewall rule.
4. **Incremental dbt models** for `fct_donations` once it grows past a few million rows.
5. **dbt docs** published to a static site after each prod deploy, so analysts can browse lineage and definitions.
