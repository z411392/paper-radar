-- Planning baseline: validated SQL, not a product acceptance receipt.
-- Apply once with the migration runner; do not edit an applied migration.

-- Owner: delivery. 寄送偏好獨立於研究範圍；secrets 不入表。
CREATE TABLE delivery_subscriptions (
id TEXT NOT NULL PRIMARY KEY, reader_id TEXT NOT NULL, channel TEXT NOT NULL CHECK(channel IN ('email','rss')), enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)), timezone TEXT NOT NULL, schedule_json TEXT NOT NULL CHECK(json_valid(schedule_json)), max_items INTEGER NOT NULL CHECK(max_items>0), recipient_ref TEXT NOT NULL, policy_version INTEGER NOT NULL CHECK(policy_version>0), created_at TEXT NOT NULL, UNIQUE(reader_id,channel)
);

-- Owner: delivery. 固定的每期內容與期間 identity，不在寄送時重新生成。
CREATE TABLE digests (
id TEXT NOT NULL PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES delivery_subscriptions(id), period_key TEXT NOT NULL, cutoff_at TEXT NOT NULL, rendered_object_id TEXT REFERENCES object_registry(object_id), state TEXT NOT NULL CHECK(state IN ('prepared','queued','cancelled','sent','unknown')), created_at TEXT NOT NULL, UNIQUE(subscription_id,period_key)
);

-- Owner: delivery. 卡片引用 exact summary revision；更正通知可沒有 summary。
CREATE TABLE digest_items (
digest_id TEXT NOT NULL REFERENCES digests(id), position INTEGER NOT NULL CHECK(position>0), event_id TEXT NOT NULL REFERENCES research_events(id), work_id TEXT NOT NULL REFERENCES paper_works(id), summary_id TEXT, revision_id TEXT, item_kind TEXT NOT NULL CHECK(item_kind IN ('paper','status_notice')), PRIMARY KEY(digest_id,position), UNIQUE(digest_id,event_id), FOREIGN KEY(summary_id,revision_id,work_id) REFERENCES summary_revisions(id,revision_id,work_id), CHECK((item_kind='paper' AND summary_id IS NOT NULL AND revision_id IS NOT NULL) OR item_kind='status_notice'), FOREIGN KEY(event_id,work_id) REFERENCES research_events(id,work_id), CHECK((summary_id IS NULL AND revision_id IS NULL) OR (summary_id IS NOT NULL AND revision_id IS NOT NULL))
);

-- Owner: delivery. 每個 digest 一筆 durable request；不保證 SMTP exactly once。
CREATE TABLE delivery_outbox (
id TEXT NOT NULL PRIMARY KEY, digest_id TEXT NOT NULL UNIQUE REFERENCES digests(id), idempotency_key TEXT NOT NULL UNIQUE, payload_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','sending','provider_accepted','failed','unknown','cancelled')), workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch>0), next_attempt_at TEXT, created_at TEXT NOT NULL
);

-- Owner: delivery. 讀者×研究事件×channel 只一個通知身份。
CREATE TABLE notification_ledger (
id TEXT NOT NULL PRIMARY KEY, reader_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES research_events(id), channel TEXT NOT NULL, outbox_id TEXT NOT NULL REFERENCES delivery_outbox(id), state TEXT NOT NULL CHECK(state IN ('reserved','accepted','unknown','cancelled')), created_at TEXT NOT NULL, UNIQUE(reader_id,event_id,channel)
);

-- Owner: delivery. request／attempt／provider accepted／read 分開。
CREATE TABLE delivery_attempts (
id TEXT NOT NULL PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES delivery_outbox(id), attempt_no INTEGER NOT NULL CHECK(attempt_no>0), state TEXT NOT NULL CHECK(state IN ('sending','provider_accepted','failed','unknown')), provider_message_id TEXT, error_code TEXT, started_at TEXT NOT NULL, finished_at TEXT, UNIQUE(outbox_id,attempt_no)
);

-- Owner: delivery. 私人使用者偏好事件，不回寫為學術品質。
CREATE TABLE reader_feedback (
id TEXT NOT NULL PRIMARY KEY, reader_id TEXT NOT NULL, work_id TEXT NOT NULL REFERENCES paper_works(id), action TEXT NOT NULL CHECK(action IN ('useful','relevant_not_urgent','irrelevant','read','saved','unsaved')), profile_id TEXT REFERENCES watch_profiles(id), created_at TEXT NOT NULL
);
