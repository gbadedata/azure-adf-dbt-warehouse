-- ctl.watermarks: the high watermark per source entity. Only moves forward.
IF OBJECT_ID(N'ctl.watermarks', N'U') IS NULL
CREATE TABLE ctl.watermarks (
    source_name     NVARCHAR(50)      NOT NULL,
    entity          NVARCHAR(100)     NOT NULL,
    high_watermark  DATETIMEOFFSET(3) NOT NULL,
    updated_at      DATETIME2(3)      NOT NULL,
    CONSTRAINT pk_watermarks PRIMARY KEY (source_name, entity)
);
GO

-- ctl.ingestion_runs: one row per entity or file per run, written by the loaders.
IF OBJECT_ID(N'ctl.ingestion_runs', N'U') IS NULL
CREATE TABLE ctl.ingestion_runs (
    run_id           NVARCHAR(36)      NOT NULL CONSTRAINT pk_ingestion_runs PRIMARY KEY,
    source_name      NVARCHAR(50)      NOT NULL,
    entity           NVARCHAR(100)     NOT NULL,
    source_file      NVARCHAR(400)     NULL,
    started_at       DATETIMEOFFSET(3) NOT NULL,
    finished_at      DATETIMEOFFSET(3) NULL,
    status           NVARCHAR(20)      NOT NULL
        CONSTRAINT ck_ingestion_runs_status CHECK (status IN (N'running', N'succeeded', N'failed')),
    requested_since  DATETIMEOFFSET(3) NULL,
    new_watermark    DATETIMEOFFSET(3) NULL,
    pages            INT NULL,
    rows_received    INT NULL,
    rows_inserted    INT NULL,
    rows_updated     INT NULL,
    rows_unchanged   INT NULL,
    api_requests     INT NULL,
    api_retries      INT NULL,
    error_message    NVARCHAR(4000) NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_ingestion_runs_entity_started'
               AND object_id = OBJECT_ID(N'ctl.ingestion_runs'))
CREATE INDEX ix_ingestion_runs_entity_started ON ctl.ingestion_runs (entity, started_at DESC);
GO
