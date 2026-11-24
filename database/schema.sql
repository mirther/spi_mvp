CREATE INDEX ix_auth_login_attempts_attempt_key ON auth_login_attempts (attempt_key);

CREATE INDEX ix_auth_login_attempts_attempted_at ON auth_login_attempts (attempted_at);

CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE UNIQUE INDEX ix_auth_users_username ON auth_users (username);

CREATE INDEX ix_data_assets_sha256 ON data_assets (sha256);

CREATE INDEX ix_hpc_access_leases_expires_at ON hpc_access_leases (expires_at);

CREATE INDEX ix_pipeline_connections_project_id ON pipeline_connections (project_id);

CREATE INDEX ix_pipeline_nodes_project_id ON pipeline_nodes (project_id);

CREATE INDEX ix_project_runs_project_id ON project_runs (project_id);

CREATE INDEX ix_run_executions_slurm_job_id ON run_executions (slurm_job_id);

CREATE TABLE auth_login_attempts (
	id INTEGER NOT NULL, 
	attempt_key VARCHAR(64) NOT NULL, 
	attempted_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE auth_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	csrf_token_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	last_activity_at DATETIME NOT NULL, 
	expires_at DATETIME NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES auth_users (id) ON DELETE CASCADE
);

CREATE TABLE auth_users (
	id VARCHAR(64) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE cluster_configurations (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	job_name VARCHAR(128) NOT NULL, 
	partition VARCHAR(128) NOT NULL, 
	stdout VARCHAR(255) NOT NULL, 
	stderr VARCHAR(255) NOT NULL, 
	nodes INTEGER NOT NULL, 
	tasks INTEGER NOT NULL, 
	cpus INTEGER NOT NULL, 
	time VARCHAR(32) NOT NULL, 
	memory VARCHAR(32) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE data_assets (
	id CHAR(32) NOT NULL, 
	original_filename VARCHAR(255) NOT NULL, 
	remote_path VARCHAR(1024) NOT NULL, 
	file_size_bytes BIGINT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	trace_count INTEGER, 
	samples_per_trace INTEGER, 
	sample_interval_us INTEGER, 
	sample_format_code INTEGER, 
	status VARCHAR(20) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (remote_path)
);

CREATE TABLE hpc_access_leases (
	id INTEGER NOT NULL, 
	fingerprint VARCHAR(128) NOT NULL, 
	public_key_path VARCHAR(1024) NOT NULL, 
	connected_at DATETIME NOT NULL, 
	expires_at DATETIME NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE pipeline_connections (
	id VARCHAR(64) NOT NULL, 
	project_id VARCHAR(64) NOT NULL, 
	source_node_id VARCHAR(64) NOT NULL, 
	target_node_id VARCHAR(64) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_project_pipeline_connection UNIQUE (project_id, source_node_id, target_node_id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE TABLE pipeline_nodes (
	id VARCHAR(64) NOT NULL, 
	project_id VARCHAR(64) NOT NULL, 
	module_id VARCHAR(128) NOT NULL, 
	x FLOAT NOT NULL, 
	y FLOAT NOT NULL, 
	parameters JSON NOT NULL, 
	sort_order INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE TABLE project_runs (
	id VARCHAR(64) NOT NULL, 
	project_id VARCHAR(64) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	started_at VARCHAR(64) NOT NULL, 
	stdout TEXT, 
	stderr TEXT, 
	sort_order INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE TABLE projects (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	description TEXT NOT NULL, 
	cluster_config_id VARCHAR(64) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE run_executions (
	run_id VARCHAR(64) NOT NULL, 
	execution_mode VARCHAR(20) NOT NULL, 
	slurm_job_id VARCHAR(64), 
	request_path TEXT NOT NULL, 
	batch_path TEXT NOT NULL, 
	stdout_pattern TEXT NOT NULL, 
	stderr_pattern TEXT NOT NULL, 
	submission_error TEXT, 
	PRIMARY KEY (run_id), 
	FOREIGN KEY(run_id) REFERENCES project_runs (id) ON DELETE CASCADE
);
