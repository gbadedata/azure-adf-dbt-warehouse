-- Raw landing tables. API records are stored as the JSON the API returned, so a
-- new field in the source never breaks a load; dbt staging models pick out the
-- fields the warehouse uses.
IF OBJECT_ID(N'raw.crm_campaigns', N'U') IS NULL
CREATE TABLE raw.crm_campaigns (
    record_id          NVARCHAR(100)     NOT NULL CONSTRAINT pk_crm_campaigns PRIMARY KEY,
    source_updated_at  DATETIMEOFFSET(3) NOT NULL,
    payload            NVARCHAR(MAX)     NOT NULL CONSTRAINT ck_crm_campaigns_json CHECK (ISJSON(payload) = 1),
    _first_loaded_at   DATETIME2(3)      NOT NULL,
    _last_loaded_at    DATETIME2(3)      NOT NULL,
    _run_id            NVARCHAR(36)      NOT NULL
);
GO

IF OBJECT_ID(N'raw.crm_supporters', N'U') IS NULL
CREATE TABLE raw.crm_supporters (
    record_id          NVARCHAR(100)     NOT NULL CONSTRAINT pk_crm_supporters PRIMARY KEY,
    source_updated_at  DATETIMEOFFSET(3) NOT NULL,
    payload            NVARCHAR(MAX)     NOT NULL CONSTRAINT ck_crm_supporters_json CHECK (ISJSON(payload) = 1),
    _first_loaded_at   DATETIME2(3)      NOT NULL,
    _last_loaded_at    DATETIME2(3)      NOT NULL,
    _run_id            NVARCHAR(36)      NOT NULL
);
GO

IF OBJECT_ID(N'raw.crm_donations', N'U') IS NULL
CREATE TABLE raw.crm_donations (
    record_id          NVARCHAR(100)     NOT NULL CONSTRAINT pk_crm_donations PRIMARY KEY,
    source_updated_at  DATETIMEOFFSET(3) NOT NULL,
    payload            NVARCHAR(MAX)     NOT NULL CONSTRAINT ck_crm_donations_json CHECK (ISJSON(payload) = 1),
    _first_loaded_at   DATETIME2(3)      NOT NULL,
    _last_loaded_at    DATETIME2(3)      NOT NULL,
    _run_id            NVARCHAR(36)      NOT NULL
);
GO

-- Event-registration exports arrive as CSV files. In Azure, Data Factory's Copy
-- activity upserts them here on registration_id; in CI the Python loader does
-- the same. Values stay as text; dbt staging types them.
IF OBJECT_ID(N'raw.event_registrations', N'U') IS NULL
CREATE TABLE raw.event_registrations (
    registration_id         NVARCHAR(50)  NOT NULL CONSTRAINT pk_event_registrations PRIMARY KEY,
    event_name              NVARCHAR(200) NULL,
    event_date              NVARCHAR(30)  NULL,
    campaign_id             NVARCHAR(50)  NULL,
    supporter_email         NVARCHAR(320) NULL,
    registered_at           NVARCHAR(40)  NULL,
    ticket_type             NVARCHAR(50)  NULL,
    fundraising_target_gbp  NVARCHAR(30)  NULL,
    _source_file            NVARCHAR(400) NULL,
    _last_loaded_at         DATETIME2(3)  NULL
);
GO
