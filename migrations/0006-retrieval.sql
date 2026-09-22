-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: retrieval. 對原始語意內容的檢索投影，不是 catalog authority。
CREATE TABLE search_documents (
id TEXT NOT NULL PRIMARY KEY, work_id TEXT NOT NULL REFERENCES paper_works(id), revision_id TEXT NOT NULL REFERENCES paper_revisions(id), projection_kind TEXT NOT NULL, text_object_id TEXT NOT NULL REFERENCES object_registry(object_id), input_fingerprint TEXT NOT NULL, is_current INTEGER NOT NULL CHECK(is_current IN (0,1)), sequence_no INTEGER NOT NULL UNIQUE, UNIQUE(work_id,projection_kind,input_fingerprint), FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id)
);

CREATE UNIQUE INDEX search_document_current ON search_documents(work_id,projection_kind) WHERE is_current=1;

CREATE VIRTUAL TABLE search_documents_fts USING fts5(document_id UNINDEXED, title, abstract, explanation, tokenize='unicode61');

-- Owner: retrieval. 模型版本及處理方式決定向量空間，dimension 不寫死。
CREATE TABLE embedding_spaces (
id TEXT NOT NULL PRIMARY KEY, provider TEXT NOT NULL, model_name TEXT NOT NULL, model_revision TEXT NOT NULL, dimension INTEGER NOT NULL CHECK(dimension>0), dtype TEXT NOT NULL CHECK(dtype='float32'), normalization_version TEXT NOT NULL, prefix_config_hash TEXT NOT NULL, metric TEXT NOT NULL CHECK(metric IN ('inner_product','l2')), configuration_fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);

-- Owner: retrieval. SQLite 配發穩定 int64 ID，原始向量另存不可變 npy。
CREATE TABLE embeddings (
id INTEGER PRIMARY KEY AUTOINCREMENT CHECK(id>0), document_id TEXT NOT NULL REFERENCES search_documents(id), space_id TEXT NOT NULL REFERENCES embedding_spaces(id), object_id TEXT NOT NULL REFERENCES object_registry(object_id), row_offset INTEGER NOT NULL CHECK(row_offset>=0), input_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(document_id,space_id,input_fingerprint), UNIQUE(id,space_id)
);

-- Owner: retrieval. 私有建置、校驗、發布的 FAISS generation。
CREATE TABLE index_generations (
id TEXT NOT NULL PRIMARY KEY, space_id TEXT NOT NULL REFERENCES embedding_spaces(id), state TEXT NOT NULL CHECK(state IN ('building','ready','failed')), relative_directory TEXT NOT NULL UNIQUE, manifest_sha256 TEXT, index_sha256 TEXT, membership_digest TEXT, document_high_watermark INTEGER NOT NULL, vector_count INTEGER NOT NULL CHECK(vector_count>=0), created_at TEXT NOT NULL, verified_at TEXT, UNIQUE(id,space_id), CHECK(state<>'ready' OR (manifest_sha256 IS NOT NULL AND index_sha256 IS NOT NULL AND membership_digest IS NOT NULL AND verified_at IS NOT NULL))
);

-- Owner: retrieval. 同一 generation 只容許相同向量空間的成員。
CREATE TABLE index_generation_members (
generation_id TEXT NOT NULL, embedding_id INTEGER NOT NULL, space_id TEXT NOT NULL, PRIMARY KEY(generation_id,embedding_id), FOREIGN KEY(generation_id,space_id) REFERENCES index_generations(id,space_id), FOREIGN KEY(embedding_id,space_id) REFERENCES embeddings(id,space_id)
);

-- Owner: retrieval. active 唯一權威在 SQLite，不另建 current.json。
CREATE TABLE active_indexes (
space_id TEXT NOT NULL PRIMARY KEY REFERENCES embedding_spaces(id), generation_id TEXT NOT NULL, pointer_version INTEGER NOT NULL CHECK(pointer_version>0), activated_at TEXT NOT NULL, FOREIGN KEY(generation_id,space_id) REFERENCES index_generations(id,space_id)
);

CREATE TRIGGER active_index_ready_insert BEFORE INSERT ON active_indexes WHEN (SELECT state FROM index_generations WHERE id=NEW.generation_id)<>'ready' BEGIN SELECT RAISE(ABORT,'generation_not_ready'); END;

CREATE TRIGGER active_index_ready_update BEFORE UPDATE ON active_indexes WHEN (SELECT state FROM index_generations WHERE id=NEW.generation_id)<>'ready' BEGIN SELECT RAISE(ABORT,'generation_not_ready'); END;
