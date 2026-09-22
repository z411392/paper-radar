-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: watch_profiles. 五領域種子與別名；新增已覆蓋領域不改 code。
CREATE TABLE domain_definitions (
id TEXT NOT NULL, name TEXT NOT NULL, definition_json TEXT NOT NULL CHECK(json_valid(definition_json)), revision INTEGER NOT NULL CHECK(revision>0), updated_at TEXT NOT NULL, PRIMARY KEY(id,revision)
);

-- Owner: watch_profiles. 讀者關注身份；刪除範圍不刪歷史。
CREATE TABLE watch_profiles (
id TEXT NOT NULL PRIMARY KEY, reader_id TEXT NOT NULL, name TEXT NOT NULL, lifecycle TEXT NOT NULL CHECK(lifecycle IN ('active','paused','archived')), published_revision INTEGER, created_at TEXT NOT NULL, FOREIGN KEY(id,published_revision) REFERENCES watch_profile_revisions(profile_id,revision) DEFERRABLE INITIALLY DEFERRED
);

-- Owner: watch_profiles. 不可變的正式關注內容。
CREATE TABLE watch_profile_revisions (
profile_id TEXT NOT NULL REFERENCES watch_profiles(id), revision INTEGER NOT NULL CHECK(revision>0), scope_text TEXT NOT NULL, filters_json TEXT NOT NULL CHECK(json_valid(filters_json)), fingerprint TEXT NOT NULL, published_at TEXT NOT NULL, PRIMARY KEY(profile_id,revision), UNIQUE(profile_id,fingerprint)
);

-- Owner: watch_profiles. 關注修訂與領域多對多。
CREATE TABLE watch_profile_domains (
profile_id TEXT NOT NULL, revision INTEGER NOT NULL, domain_id TEXT NOT NULL, domain_revision INTEGER NOT NULL, PRIMARY KEY(profile_id,revision,domain_id), FOREIGN KEY(profile_id,revision) REFERENCES watch_profile_revisions(profile_id,revision), FOREIGN KEY(domain_id,domain_revision) REFERENCES domain_definitions(id,revision)
);
