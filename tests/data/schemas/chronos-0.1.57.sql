-- Chronos schema as created by 0.1.57 (last release before chained events).
-- Generated with Base.metadata.create_all from the models at git tag release-v0.1.57.
-- Used by tests/test_schema_upgrade.py as an upgrade starting point; do not edit by hand.

CREATE TABLE scheduled_jobs (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	task_type VARCHAR(26) NOT NULL, 
	cron_expression VARCHAR NOT NULL, 
	pipeline_id VARCHAR NOT NULL, 
	entity_ids TEXT, 
	group_ids TEXT, 
	with_snapshot BOOLEAN, 
	snapshot_write_method VARCHAR NOT NULL, 
	enabled BOOLEAN, 
	command TEXT NOT NULL, 
	cron_job_identifier VARCHAR NOT NULL, 
	created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	last_run DATETIME, 
	next_run DATETIME, 
	last_successful_run DATETIME, 
	last_error_message TEXT, 
	last_run_error_time DATETIME, 
	start_time VARCHAR, 
	timezone_name VARCHAR, 
	is_cron_expression BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (cron_job_identifier)
);

CREATE TABLE settings (
	id INTEGER NOT NULL, 
	"key" VARCHAR NOT NULL, 
	value VARCHAR, 
	description VARCHAR, 
	created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_scheduled_jobs_id ON scheduled_jobs (id);

CREATE INDEX ix_settings_id ON settings (id);

CREATE UNIQUE INDEX ix_settings_key ON settings ("key");
