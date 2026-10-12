-- Chronos schema as created by 0.2.0, 0.2.1 and 0.2.2 (identical schema).
-- Generated with Base.metadata.create_all from the models at git tag release-v0.2.0.
-- Used by tests/test_schema_upgrade.py as an upgrade starting point; do not edit by hand.

CREATE TABLE chained_job_events (
	id INTEGER NOT NULL, 
	parent_job_id INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	task_type VARCHAR(26) NOT NULL, 
	pipeline_id VARCHAR NOT NULL, 
	entity_ids TEXT, 
	group_ids TEXT, 
	with_snapshot BOOLEAN, 
	snapshot_write_method VARCHAR NOT NULL, 
	execution_mode VARCHAR(5) NOT NULL, 
	webhook_timeout_seconds INTEGER NOT NULL, 
	created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(parent_job_id) REFERENCES scheduled_jobs (id) ON DELETE CASCADE
);

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

CREATE TABLE trigger_flow_events (
	id INTEGER NOT NULL, 
	trigger_flow_id INTEGER NOT NULL, 
	position INTEGER NOT NULL, 
	task_type VARCHAR(26) NOT NULL, 
	pipeline_id VARCHAR NOT NULL, 
	entity_ids TEXT, 
	group_ids TEXT, 
	with_snapshot BOOLEAN, 
	snapshot_write_method VARCHAR NOT NULL, 
	execution_mode VARCHAR(5) NOT NULL, 
	created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(trigger_flow_id) REFERENCES trigger_flows (id) ON DELETE CASCADE
);

CREATE TABLE trigger_flow_execution_logs (
	id INTEGER NOT NULL, 
	trigger_flow_id INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	source VARCHAR, 
	error_message TEXT, 
	duration_ms INTEGER, 
	triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(trigger_flow_id) REFERENCES trigger_flows (id) ON DELETE CASCADE
);

CREATE TABLE trigger_flows (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	description VARCHAR, 
	enabled BOOLEAN NOT NULL, 
	platform_event VARCHAR, 
	secret_token VARCHAR NOT NULL, 
	last_triggered TIMESTAMP, 
	last_successful_trigger TIMESTAMP, 
	last_error_message TEXT, 
	last_trigger_error_time TIMESTAMP, 
	created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_chained_job_events_id ON chained_job_events (id);

CREATE INDEX ix_chained_job_events_parent_job_id ON chained_job_events (parent_job_id);

CREATE INDEX ix_scheduled_jobs_id ON scheduled_jobs (id);

CREATE INDEX ix_settings_id ON settings (id);

CREATE UNIQUE INDEX ix_settings_key ON settings ("key");

CREATE INDEX ix_trigger_flow_events_id ON trigger_flow_events (id);

CREATE INDEX ix_trigger_flow_events_trigger_flow_id ON trigger_flow_events (trigger_flow_id);

CREATE INDEX ix_trigger_flow_execution_logs_id ON trigger_flow_execution_logs (id);

CREATE INDEX ix_trigger_flow_execution_logs_trigger_flow_id ON trigger_flow_execution_logs (trigger_flow_id);

CREATE INDEX ix_trigger_flows_id ON trigger_flows (id);
