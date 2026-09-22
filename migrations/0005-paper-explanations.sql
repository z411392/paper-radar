-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: paper_explanations. 模型、prompt、input、實際用量與失敗，不記 secrets。
CREATE TABLE model_runs (
id TEXT NOT NULL PRIMARY KEY, task_kind TEXT NOT NULL, provider TEXT NOT NULL, model_name TEXT NOT NULL, model_revision TEXT, prompt_digest TEXT NOT NULL, input_fingerprint TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('reserved','running','succeeded','failed','budget_blocked','stale')), output_object_id TEXT REFERENCES object_registry(object_id), input_tokens INTEGER CHECK(input_tokens>=0), output_tokens INTEGER CHECK(output_tokens>=0), actual_cost_micros INTEGER CHECK(actual_cost_micros>=0), error_code TEXT, started_at TEXT NOT NULL, finished_at TEXT
);

-- Owner: paper_explanations. 呼叫前保留預算，失敗也對帳實際使用。
CREATE TABLE usage_reservations (
id TEXT NOT NULL PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES model_runs(id), period_key TEXT NOT NULL, currency TEXT NOT NULL, reserved_micros INTEGER NOT NULL CHECK(reserved_micros>=0), actual_micros INTEGER CHECK(actual_micros>=0), state TEXT NOT NULL CHECK(state IN ('reserved','settled','released','unknown')), created_at TEXT NOT NULL
);

-- Owner: paper_explanations. 作者報告的目的、方法、資料、結果與限制。
CREATE TABLE paper_claims (
id TEXT NOT NULL PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(id), extraction_run_id TEXT NOT NULL REFERENCES model_runs(id), claim_type TEXT NOT NULL CHECK(claim_type IN ('objective','method','data','result','limitation','author_interpretation')), claim_json TEXT NOT NULL CHECK(json_valid(claim_json)), UNIQUE(id,snapshot_id)
);

-- Owner: paper_explanations. claim 與 anchor 必須出自同一 evidence snapshot。
CREATE TABLE claim_anchors (
claim_id TEXT NOT NULL, anchor_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, PRIMARY KEY(claim_id,anchor_id), FOREIGN KEY(claim_id,snapshot_id) REFERENCES paper_claims(id,snapshot_id), FOREIGN KEY(anchor_id,snapshot_id) REFERENCES evidence_anchors(id,snapshot_id)
);

-- Owner: paper_explanations. 輸出不可變，current pointer 與執行狀態分開。
CREATE TABLE summary_revisions (
id TEXT NOT NULL PRIMARY KEY, revision_id TEXT NOT NULL, work_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, generation_fingerprint TEXT NOT NULL UNIQUE, generation_run_id TEXT NOT NULL REFERENCES model_runs(id), output_object_id TEXT NOT NULL REFERENCES object_registry(object_id), language TEXT NOT NULL, explanation_profile TEXT NOT NULL, qa_state TEXT NOT NULL CHECK(qa_state IN ('pending','passed','rejected')), created_at TEXT NOT NULL, FOREIGN KEY(snapshot_id,revision_id,work_id) REFERENCES evidence_snapshots(id,revision_id,work_id), UNIQUE(id,revision_id,work_id), UNIQUE(id,snapshot_id)
);

-- Owner: paper_explanations. 每個重要解說句對應抽取 claim。
CREATE TABLE summary_claims (
summary_id TEXT NOT NULL, claim_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, output_locator TEXT NOT NULL, PRIMARY KEY(summary_id,claim_id,output_locator), FOREIGN KEY(summary_id,snapshot_id) REFERENCES summary_revisions(id,snapshot_id), FOREIGN KEY(claim_id,snapshot_id) REFERENCES paper_claims(id,snapshot_id)
);

-- Owner: paper_explanations. 程式數值檢查與逐句 verifier 各有證據。
CREATE TABLE verification_results (
id TEXT NOT NULL PRIMARY KEY, summary_id TEXT NOT NULL REFERENCES summary_revisions(id), verifier_kind TEXT NOT NULL, run_id TEXT REFERENCES model_runs(id), report_object_id TEXT NOT NULL REFERENCES object_registry(object_id), verdict TEXT NOT NULL CHECK(verdict IN ('passed','rejected','failed')), verified_at TEXT NOT NULL
);

-- Owner: paper_explanations. 發布必須以 CAS 核對當前 input fingerprint。
CREATE TABLE current_summaries (
work_id TEXT NOT NULL REFERENCES paper_works(id), language TEXT NOT NULL, explanation_profile TEXT NOT NULL, summary_id TEXT NOT NULL, revision_id TEXT NOT NULL, expected_input_fingerprint TEXT NOT NULL, pointer_version INTEGER NOT NULL CHECK(pointer_version>0), PRIMARY KEY(work_id,language,explanation_profile), FOREIGN KEY(summary_id,revision_id,work_id) REFERENCES summary_revisions(id,revision_id,work_id)
);

-- Owner: watch_profiles. 相關性及私人推薦理由，不改研究事實。
CREATE TABLE relevance_assessments (
id TEXT NOT NULL PRIMARY KEY, profile_id TEXT NOT NULL, profile_revision INTEGER NOT NULL, revision_id TEXT NOT NULL REFERENCES paper_revisions(id), input_fingerprint TEXT NOT NULL, decision TEXT CHECK(decision IN ('direct','adjacent','uncertain','irrelevant')), execution_state TEXT NOT NULL CHECK(execution_state IN ('pending','succeeded','failed','stale')), reason_json TEXT CHECK(reason_json IS NULL OR json_valid(reason_json)), assessed_at TEXT NOT NULL, FOREIGN KEY(profile_id,profile_revision) REFERENCES watch_profile_revisions(profile_id,revision), CHECK(execution_state<>'succeeded' OR decision IS NOT NULL)
);
