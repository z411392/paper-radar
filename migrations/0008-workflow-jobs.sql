-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: research_workflow. 應用排程與持久工作，單一 job 可有多個 attempts。
CREATE TABLE workflow_jobs (
id TEXT NOT NULL PRIMARY KEY, job_kind TEXT NOT NULL, business_key TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL CHECK(json_valid(input_json)), input_fingerprint TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','cancelled','budget_blocked','awaiting_external')), due_at TEXT NOT NULL, lease_owner TEXT, lease_until TEXT, fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token>=0), attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0), created_at TEXT NOT NULL
);

-- Owner: research_workflow. execution 收據不可當 acceptance。
CREATE TABLE job_attempts (
id TEXT NOT NULL PRIMARY KEY, job_id TEXT NOT NULL REFERENCES workflow_jobs(id), attempt_no INTEGER NOT NULL CHECK(attempt_no>0), fencing_token INTEGER NOT NULL, state TEXT NOT NULL, error_code TEXT, started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(job_id,attempt_no)
);

-- Owner: research_workflow. 短交易記錄下游待辦，避免跨 BC 寫入後漏排程。
CREATE TABLE workflow_events (
id TEXT NOT NULL PRIMARY KEY, source_kind TEXT NOT NULL, source_identity TEXT NOT NULL, event_kind TEXT NOT NULL, business_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), consumed_at TEXT, created_at TEXT NOT NULL
);

-- Owner: research_workflow. 備份 manifest 的識別，不把可重建索引當唯一原始資料。
CREATE TABLE backup_runs (
id TEXT NOT NULL PRIMARY KEY, workspace_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('building','verified','failed')), relative_directory TEXT NOT NULL, database_sha256 TEXT, manifest_sha256 TEXT, object_count INTEGER CHECK(object_count>=0), created_at TEXT NOT NULL, verified_at TEXT
);
