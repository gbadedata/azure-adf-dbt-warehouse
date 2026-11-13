-- Empties loaded data so the demonstration starts from nothing.
-- demo_run.sh runs this against the demo deployment only.
DELETE FROM ctl.ingestion_runs;
DELETE FROM ctl.watermarks;
DELETE FROM raw.crm_campaigns;
DELETE FROM raw.crm_supporters;
DELETE FROM raw.crm_donations;
DELETE FROM raw.event_registrations;
GO
