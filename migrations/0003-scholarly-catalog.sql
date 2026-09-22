-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: scholarly_catalog. 穩定的研究本體；展示欄位需 field provenance。
CREATE TABLE paper_works (
id TEXT NOT NULL PRIMARY KEY, canonical_title TEXT NOT NULL, publication_status TEXT NOT NULL CHECK(publication_status IN ('preprint','published','corrected','retracted','withdrawn','unknown')), first_public_date TEXT, first_public_precision TEXT CHECK(first_public_precision IN ('year','month','day','second')), first_seen_at TEXT NOT NULL, created_at TEXT NOT NULL
);

-- Owner: scholarly_catalog. 合併後的舊 identity 仍可解析；不得丟已寄歷史。
CREATE TABLE work_aliases (
alias_work_id TEXT NOT NULL PRIMARY KEY REFERENCES paper_works(id), canonical_work_id TEXT NOT NULL REFERENCES paper_works(id), decision_evidence_json TEXT NOT NULL CHECK(json_valid(decision_evidence_json)), created_at TEXT NOT NULL, CHECK(alias_work_id<>canonical_work_id)
);

-- Owner: scholarly_catalog. 預印本 repository 或正式出版呈現，不把 version 放在 work identity。
CREATE TABLE paper_manifestations (
id TEXT NOT NULL PRIMARY KEY, work_id TEXT NOT NULL REFERENCES paper_works(id), source_namespace TEXT NOT NULL, native_id TEXT NOT NULL, manifestation_kind TEXT NOT NULL CHECK(manifestation_kind IN ('preprint','publication','notice','repository_copy')), landing_url TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_namespace,native_id), UNIQUE(id,work_id)
);

-- Owner: scholarly_catalog. 一份 manifestation 的不可變內容修訂。
CREATE TABLE paper_revisions (
id TEXT NOT NULL PRIMARY KEY, manifestation_id TEXT NOT NULL, work_id TEXT NOT NULL, native_version TEXT, content_fingerprint TEXT NOT NULL, title TEXT NOT NULL, abstract_object_id TEXT REFERENCES object_registry(object_id), source_updated_at TEXT, published_date TEXT, date_precision TEXT CHECK(date_precision IN ('year','month','day','second')), observed_at TEXT NOT NULL, FOREIGN KEY(manifestation_id,work_id) REFERENCES paper_manifestations(id,work_id), UNIQUE(manifestation_id,content_fingerprint), UNIQUE(id,work_id)
);

-- Owner: scholarly_catalog. 識別碼歸於呈現／書目 identity，不強制每 Work 一個 DOI。
CREATE TABLE external_identifiers (
namespace TEXT NOT NULL, normalized_value TEXT NOT NULL, manifestation_id TEXT NOT NULL REFERENCES paper_manifestations(id), source_evidence_id TEXT NOT NULL, PRIMARY KEY(namespace,normalized_value)
);

-- Owner: scholarly_catalog. 保留 relation type；correction 不等同 preprint-of。
CREATE TABLE work_relations (
id TEXT NOT NULL PRIMARY KEY, source_work_id TEXT NOT NULL REFERENCES paper_works(id), target_work_id TEXT NOT NULL REFERENCES paper_works(id), relation_type TEXT NOT NULL, evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)), observed_at TEXT NOT NULL, CHECK(source_work_id<>target_work_id), UNIQUE(source_work_id,target_work_id,relation_type)
);

-- Owner: scholarly_catalog. 展示欄位及衝突來源；原始觀測仍歸 Discovery。
CREATE TABLE catalog_field_provenance (
id TEXT NOT NULL PRIMARY KEY, work_id TEXT NOT NULL REFERENCES paper_works(id), field_name TEXT NOT NULL, value_json TEXT NOT NULL CHECK(json_valid(value_json)), source_observation_id TEXT NOT NULL, preference_reason TEXT, observed_at TEXT NOT NULL
);

-- Owner: scholarly_catalog. 免費閱讀、自動取得及用途各自保存證據和未知原因。
CREATE TABLE access_assessments (
id TEXT NOT NULL PRIMARY KEY, manifestation_id TEXT NOT NULL REFERENCES paper_manifestations(id), location_url TEXT NOT NULL, reader_access TEXT NOT NULL CHECK(reader_access IN ('free','restricted','unknown')), automated_retrieval TEXT NOT NULL CHECK(automated_retrieval IN ('permitted','prohibited','unknown')), permitted_uses_json TEXT NOT NULL CHECK(json_valid(permitted_uses_json)), license_id TEXT, evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)), checked_at TEXT NOT NULL, expires_at TEXT, content_version_binding TEXT
);

-- Owner: scholarly_catalog. 本次實際讀取的文本與覆蓋範圍，不等於下載成功。
CREATE TABLE evidence_snapshots (
id TEXT NOT NULL PRIMARY KEY, revision_id TEXT NOT NULL, work_id TEXT NOT NULL, object_id TEXT NOT NULL REFERENCES object_registry(object_id), text_object_id TEXT NOT NULL REFERENCES object_registry(object_id), parser_version TEXT NOT NULL, evidence_level TEXT NOT NULL CHECK(evidence_level IN ('abstract_only','selected_sections','full_text')), coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json)), fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id), UNIQUE(id,revision_id,work_id)
);

-- Owner: scholarly_catalog. Python Unicode 字元 offset；表格 anchor 附 row/column label。
CREATE TABLE evidence_anchors (
id TEXT NOT NULL PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(id), section_label TEXT, paragraph_id TEXT, quote TEXT NOT NULL CHECK(length(quote)>0), offset_start INTEGER NOT NULL CHECK(offset_start>=0), offset_end INTEGER NOT NULL CHECK(offset_end>offset_start), table_locator_json TEXT CHECK(table_locator_json IS NULL OR json_valid(table_locator_json)), UNIQUE(id,snapshot_id)
);

-- Owner: scholarly_catalog. 新文／補收錄／修訂／出版／更正／撤稿為不同事件。
CREATE TABLE research_events (
id TEXT NOT NULL PRIMARY KEY, work_id TEXT NOT NULL REFERENCES paper_works(id), revision_id TEXT REFERENCES paper_revisions(id), event_kind TEXT NOT NULL CHECK(event_kind IN ('new_work','late_discovery','revision_available','publication_status_changed','correction','retraction','newly_accessible','metadata_changed')), canonical_event_key TEXT NOT NULL UNIQUE, source_evidence_json TEXT NOT NULL CHECK(json_valid(source_evidence_json)), occurred_at TEXT, observed_at TEXT NOT NULL, UNIQUE(id,work_id), FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id)
);
