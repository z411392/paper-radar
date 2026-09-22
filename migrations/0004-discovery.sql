-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: discovery. 精確 query 與設定 identity；每個 binding 獨立進度。
CREATE TABLE source_bindings (
id TEXT NOT NULL PRIMARY KEY, profile_id TEXT NOT NULL, profile_revision INTEGER NOT NULL, source TEXT NOT NULL, config_revision INTEGER NOT NULL, compiled_query_json TEXT NOT NULL CHECK(json_valid(compiled_query_json)), query_fingerprint TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), FOREIGN KEY(profile_id,profile_revision) REFERENCES watch_profile_revisions(profile_id,revision), UNIQUE(profile_id,profile_revision,source,query_fingerprint)
);

-- Owner: discovery. 邏輯收集時間窗與 checkpoint；attempt 與工作不同。
CREATE TABLE harvest_units (
id TEXT NOT NULL PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES source_bindings(id), window_start TEXT NOT NULL, window_end TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','verified_empty','partial','failed','unavailable')), cursor_json TEXT CHECK(cursor_json IS NULL OR json_valid(cursor_json)), checkpoint_version INTEGER NOT NULL DEFAULT 0, coverage_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(coverage_json)), created_at TEXT NOT NULL, CHECK(window_start<window_end), UNIQUE(binding_id,window_start,window_end)
);

-- Owner: discovery. 每次 source 呼叫／中斷有單獨收據。
CREATE TABLE harvest_attempts (
id TEXT NOT NULL PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES harvest_units(id), attempt_no INTEGER NOT NULL CHECK(attempt_no>0), state TEXT NOT NULL, error_code TEXT, response_metadata_json TEXT CHECK(response_metadata_json IS NULL OR json_valid(response_metadata_json)), started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(unit_id,attempt_no)
);

-- Owner: discovery. raw payload 的唯一原始觀测 owner。
CREATE TABLE source_observations (
id TEXT NOT NULL PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES harvest_units(id), source TEXT NOT NULL, native_id TEXT NOT NULL, payload_object_id TEXT NOT NULL REFERENCES object_registry(object_id), parser_version TEXT NOT NULL, observed_at TEXT NOT NULL, native_updated_at TEXT, UNIQUE(unit_id,source,native_id,payload_object_id,parser_version)
);

-- Owner: discovery. provider 共用節流，不以領域分開避開總限制。
CREATE TABLE provider_budgets (
source TEXT NOT NULL PRIMARY KEY, next_allowed_at TEXT NOT NULL, config_json TEXT NOT NULL CHECK(json_valid(config_json)), updated_at TEXT NOT NULL
);
