-- Schemas for loading. dbt creates its own schemas (staging, intermediate, marts).
-- Every script in this folder is safe to run again: python -m ingest init-db
IF SCHEMA_ID(N'ctl') IS NULL EXEC(N'CREATE SCHEMA ctl');
GO
IF SCHEMA_ID(N'raw') IS NULL EXEC(N'CREATE SCHEMA raw');
GO
