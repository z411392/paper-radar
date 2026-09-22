-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: kernel. 已套用的 migration digest；不可只靠檔名跳過修改。
CREATE TABLE schema_migrations (
version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL
);

-- Owner: kernel. 僅在不可變檔案完成發布與校驗後登錄；不取代檔案本體。
CREATE TABLE object_registry (
object_id TEXT NOT NULL PRIMARY KEY,
content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
relative_path TEXT NOT NULL UNIQUE CHECK(substr(relative_path,1,1)<>'/' AND instr(relative_path,'..')=0),
kind TEXT NOT NULL CHECK(kind IN ('raw','fulltext','extracted','evidence','model_output','embedding','digest')),
media_type TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size>=0),
state TEXT NOT NULL DEFAULT 'available' CHECK(state IN ('available','missing','quarantined')),
created_at TEXT NOT NULL, retention_policy TEXT NOT NULL,
UNIQUE(kind,content_sha256)
);

-- Owner: kernel. 單一 workspace identity；restore 使用新 epoch 並暫停副作用。
CREATE TABLE workspace_metadata (
singleton INTEGER PRIMARY KEY CHECK(singleton=1), workspace_id TEXT NOT NULL UNIQUE, epoch INTEGER NOT NULL CHECK(epoch>0), external_effects_enabled INTEGER NOT NULL DEFAULT 0 CHECK(external_effects_enabled IN (0,1)), created_at TEXT NOT NULL, restored_from TEXT
);
