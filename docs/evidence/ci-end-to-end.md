# End-to-end run in CI

Every push runs [`scripts/run_e2e.sh`](../../scripts/run_e2e.sh) in GitHub Actions against a
SQL Server 2022 service container: two days of source data loaded incrementally, the second
day replayed, `dbt build`, then every campaign figure recomputed from the source files in
plain Python ([`scripts/verify_e2e.py`](../../scripts/verify_e2e.py)) and compared with the
warehouse. This is the run of 13 November 2026.

| 100 of 100 | 4 of 4 | 59 of 59 | 0 |
|:---:|:---:|:---:|:---:|
| campaign figures matching the source exactly | late-arriving donations caught by the overlap window | dbt models and tests passed | rows changed when the whole day was loaded a second time |

## Run log (`ctl.ingestion_runs`)

| Load | Requested since | Received | Inserted | Updated | Unchanged | 429s retried |
|---|---|---:|---:|---:|---:|---:|
| Day 1 campaigns | everything | 10 | 10 | 0 | 0 | 0 |
| Day 1 supporters | everything | 2,400 | 2,400 | 0 | 0 | 4 |
| Day 1 donations | everything | 7,179 | 7,179 | 0 | 0 | 12 |
| Day 1 event file | (file) | 300 | 300 | 0 | 0 | - |
| Day 2 campaigns | watermark - 30 min | 1 | 0 | 1 | 0 | 0 |
| Day 2 supporters | watermark - 30 min | 151 | 120 | 30 | 1 | 0 |
| Day 2 donations | watermark - 30 min | 420 | 354 | 65 | 1 | 1 |
| Day 2 event file | (file) | 85 | 60 | 5 | 20 | - |
| Replay, all three entities | new watermark - 30 min | 19 | 0 | 0 | 19 | 0 |
| Replay, event file | (file) | 85 | 0 | 0 | 85 | - |

## Reading it

Day 2 donations are exactly what changed in the source: 350 new gifts, 40 refunds and 25
pending gifts that cleared, plus **4 late commits**: donations stamped eight minutes
*before* the day-one high watermark that only became visible on day two. A loader that asks
for "everything after the watermark" never sees them. This one re-reads a 30-minute overlap,
and the merge only accepts strictly newer versions, so the overlap costs one re-read row and
loses nothing. [A unit test](../../tests/test_pipeline.py) shows the same day loaded with no
overlap losing exactly those four.

The mock API rate-limits every seventh request. The loader waited out each `Retry-After` and
carried on, which is the "429s retried" column.

The day 2 event file holds 60 new registrations, 5 changed ones and 20 repeated unchanged
from day 1. Only the 65 that differ are written.

The replay loads the whole of day 2 again and changes nothing, which is what makes a failed
or repeated run safe.

## Why SQL Server 2022 stands in for Azure SQL

Azure SQL Database is built on the same engine, and every feature used here (`OPENJSON`,
`MERGE`, `JSON_VALUE`, `datetimeoffset`, `HASHBYTES`) is supported by both. The
[Azure run](azure-2026-11-13.md) gave identical counts.
