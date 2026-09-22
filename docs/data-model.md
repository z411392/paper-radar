# 資料模型與本機儲存契約

本文件為技術模型 owner；共通產品行為回到 requirements，工作狀態回到 GitHub。SQL 在 migrations/；這批是可執行語法檢查的設計基線，不等於已完成 adapter、application 或 migration freeze。

## 一、邏輯結構

```text
WatchProfile → WatchProfileRevision → SourceBinding → HarvestUnit → SourceObservation
PaperWork → Manifestation → Revision → EvidenceSnapshot → Anchor
EvidenceSnapshot → PaperClaim → SummaryRevision → VerificationResult
WatchProfileRevision × Revision → RelevanceAssessment
Revision → SearchDocument → Embedding(space) → IndexGeneration → ActiveIndex
Reader × ResearchEvent → DigestItem → Outbox → DeliveryAttempt / NotificationLedger
WorkflowJob → JobAttempt；immutable ObjectRef 連到檔案 bytes
```

Work 是研究身份，Manifestation 是來源呈現，Revision 是內容版本。三者不得合成一個可任意 overwrite 的 paper row。跨資料表欄位的重複 work_id 僅用於 composite FK 約束，不是第二個 ownership。

### 型別與共通不變式

業務 ID 使用不暴露資訊的 UUID 字串；FAISS embedding ID 是 SQLite AUTOINCREMENT 配發的正 int64，不截斷 UUID 或靠 Python hash。時間戳統一固定精度 UTC ISO 8601；來源只有年／月／日時保存原精度，不補造時刻。金額用整數 micros 與幣別，NULL 表示未觀測，不等於 0。JSON 欄位驗 json_valid，仍需 DTO/schema 驗語意。

SQLite 每個連線啟用 foreign_keys、busy_timeout，啟用 WAL 並以 FULL synchronous 作保守起點；由正式 connection factory 驗證實際生效。WAL 適用本機磁碟，不把 live DB 放網路檔案系統或同步資料夾。只有一個 writer transaction；I/O、模型與大索引建置都不得持有長寫入交易。官方背景：[SQLite WAL](https://www.sqlite.org/wal.html)。

## 二、檔案與 DB 的一致性

SQLite transaction 不能把普通檔案 rename 變成同一個 ACID transaction。本系統採可恢復發布協定：

1. 在同一 filesystem 的 tmp 寫入 bytes，算 hash，flush／fsync，限制大小與相對路徑。
2. 原子 rename 至內容位址；既有相同 hash 必須核對 bytes/size，衝突隔離不覆寫；需要時同步父目錄。
3. 檔案 durable 且校驗通過後，在短 SQLite transaction 插入 object_registry、業務引用與後續 workflow event。
4. transaction 中斷只留下未引用檔案，可依 grace period、引用掃描與備份 pin 回收；不允許已提交業務資料指向未完成檔案。
5. 發現引用檔案缺失／hash 不符時標 integrity error 並停止依賴的解說或寄送；不改成空內容繼續。

Object store 只懂 bytes／hash／路徑；source rights、論文 identity、摘要和通知由 owner 管。raw observation 只由 discovery 管；catalog 存 provenance reference，不複製第二份 raw authority。

## 三、原始向量與 FAISS

初版候選為 normalized float32＋IndexIDMap2(IndexFlatIP)；實作先以精確搜尋建立 oracle，再依實測容量決定是否需要 HNSW/IVF，不預先承諾記憶體或延遲。模型與 query/document prefix、normalization、dimension 共同決定 space。FAISS 官方：[indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)、[index factory](https://github.com/facebookresearch/faiss/wiki/The-index-factory)。

原始 npy 不開 allow_pickle；保存 object digest、shape、dtype、row mapping。所有 embedding 均可由 SQLite ID 找回 original vector；不能只依賴 FAISS reconstruct 作唯一資料來源。

建置先固定 SQLite document high watermark 與成員清單，在新 generation 目錄產生 index/manifest，驗證檔案 hash、count、space 及 canary query，再標 ready。以 SQLite CAS 切 active pointer；沒有第二份 current.json。讀者固定一代 index，舊 generation 在無使用者且不受備份 pin 後才回收。建置後新增向量等下代，不冒稱已包含。

檢索回查 SQLite 的 current／刪除／權限／profile eligibility，不能把 FAISS 取出的前 k 筆直接當有效 top-k。過濾後不足時有界擴大候選；到上限仍不足就明示 incomplete candidates。FTS5／vector／hybrid 是明示模式；模型或索引缺失只可在已選定 degraded policy 下標示 lexical-only，不能靜默假成功。FTS5 unicode61 對中文切詞的品質是待測項，不宣稱已完整支援中文詞級召回。

## 四、交易與更新邊界

| 操作 | 同一短交易內 | 交易外 |
|---|---|---|
| 收集頁保存 | observations＋該 binding checkpoint＋workflow event | API 取得、raw object durable publication |
| 論文修訂 | revision／relations／research event | 來源解析與比對 |
| 解說發布 | QA reference＋current pointer CAS＋workflow event | LLM 與 deterministic QA |
| digest 排入 | digest／items／ledger reservation／outbox | immutable render publication |
| 工作接手 | claim lease＋fencing token＋attempt | 執行 source／model／index／SMTP |
| 切索引 | ready generation 與 active pointer CAS | 完整 generation 產生與校驗 |

CAS 與 fencing 比對的 row count 必須是 1；失敗表示輸入／owner 已改，不得覆蓋 current。SQL 外的適用性規則需 application 正反 tests，不以 FK 存在宣稱已全部防護。

## 五、備份與恢復

先啟用 maintenance barrier 暫停新外部副作用、publication 與 GC，等待可安全收束的工作。透過 SQLite backup API 取得一致 DB snapshot，再枚舉它引用的 immutable objects，複製並核 hash，產生 backup manifest；成功後解除 barrier。第一版選短暫 quiesce，而非先做複雜無停機備份。官方：[SQLite Backup API](https://www.sqlite.org/backup.html)。

恢復到新目錄，foreign_key_check／integrity_check、全物件 hash／schema version 通過後才切 workspace；epoch 增加，外部副作用預設 disabled。FAISS 重建。不得直接複製仍寫入的 app.sqlite3 忽略 WAL，也不得回復舊備份後自動寄送：備份時點後可能已有外部郵件成功，需 delivery reconciliation／人工核對才能啟用。

## 六、schema ownership 與欄位字典

migrations/ 是可執行 SQL 的唯一技術定義。下列欄位／約束字典由同一 schema 建庫定義產生，是帶版本的閱讀投影，不獨立手改；DDL 變更時同步重產生。行為 current eligibility、語意來源支持、provider 成功判定與跨檔案一致性仍須對應 owning use-case tests。已套用 migration 不覆寫；變更加新 migration 並在 Task 留 review 及回復方案。

### `schema_migrations`

Owner：`kernel`。已套用的 migration digest；不可只靠檔名跳過修改。

Migration：`0001`。欄位與約束：

```sql
version INTEGER PRIMARY KEY,
name TEXT NOT NULL UNIQUE,
sha256 TEXT NOT NULL,
applied_at TEXT NOT NULL
```

### `object_registry`

Owner：`kernel`。僅在不可變檔案完成發布與校驗後登錄；不取代檔案本體。

Migration：`0001`。欄位與約束：

```sql
object_id TEXT NOT NULL PRIMARY KEY,
content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
relative_path TEXT NOT NULL UNIQUE CHECK(substr(relative_path,1,1)<>'/' AND instr(relative_path,'..')=0),
kind TEXT NOT NULL CHECK(kind IN ('raw','fulltext','extracted','evidence','model_output','embedding','digest')),
media_type TEXT NOT NULL,
byte_size INTEGER NOT NULL CHECK(byte_size>=0),
state TEXT NOT NULL DEFAULT 'available' CHECK(state IN ('available','missing','quarantined')),
created_at TEXT NOT NULL,
retention_policy TEXT NOT NULL,
UNIQUE(kind,content_sha256)
```

### `workspace_metadata`

Owner：`kernel`。單一 workspace identity；restore 使用新 epoch 並暫停副作用。

Migration：`0001`。欄位與約束：

```sql
singleton INTEGER PRIMARY KEY CHECK(singleton=1),
workspace_id TEXT NOT NULL UNIQUE,
epoch INTEGER NOT NULL CHECK(epoch>0),
external_effects_enabled INTEGER NOT NULL DEFAULT 0 CHECK(external_effects_enabled IN (0,1)),
created_at TEXT NOT NULL,
restored_from TEXT
```

### `domain_definitions`

Owner：`watch_profiles`。五領域種子與別名；新增已覆蓋領域不改 code。

Migration：`0002`。欄位與約束：

```sql
id TEXT NOT NULL,
name TEXT NOT NULL,
definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
revision INTEGER NOT NULL CHECK(revision>0),
updated_at TEXT NOT NULL,
PRIMARY KEY(id,revision)
```

### `watch_profiles`

Owner：`watch_profiles`。讀者關注身份；刪除範圍不刪歷史。

Migration：`0002`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
reader_id TEXT NOT NULL,
name TEXT NOT NULL,
lifecycle TEXT NOT NULL CHECK(lifecycle IN ('active','paused','archived')),
published_revision INTEGER,
created_at TEXT NOT NULL,
FOREIGN KEY(id,published_revision) REFERENCES watch_profile_revisions(profile_id,revision) DEFERRABLE INITIALLY DEFERRED
```

### `watch_profile_revisions`

Owner：`watch_profiles`。不可變的正式關注內容。

Migration：`0002`。欄位與約束：

```sql
profile_id TEXT NOT NULL REFERENCES watch_profiles(id),
revision INTEGER NOT NULL CHECK(revision>0),
scope_text TEXT NOT NULL,
filters_json TEXT NOT NULL CHECK(json_valid(filters_json)),
fingerprint TEXT NOT NULL,
published_at TEXT NOT NULL,
PRIMARY KEY(profile_id,revision),
UNIQUE(profile_id,fingerprint)
```

### `watch_profile_domains`

Owner：`watch_profiles`。關注修訂與領域多對多。

Migration：`0002`。欄位與約束：

```sql
profile_id TEXT NOT NULL,
revision INTEGER NOT NULL,
domain_id TEXT NOT NULL,
domain_revision INTEGER NOT NULL,
PRIMARY KEY(profile_id,revision,domain_id),
FOREIGN KEY(profile_id,revision) REFERENCES watch_profile_revisions(profile_id,revision),
FOREIGN KEY(domain_id,domain_revision) REFERENCES domain_definitions(id,revision)
```

### `paper_works`

Owner：`scholarly_catalog`。穩定的研究本體；展示欄位需 field provenance。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
canonical_title TEXT NOT NULL,
publication_status TEXT NOT NULL CHECK(publication_status IN ('preprint','published','corrected','retracted','withdrawn','unknown')),
first_public_date TEXT,
first_public_precision TEXT CHECK(first_public_precision IN ('year','month','day','second')),
first_seen_at TEXT NOT NULL,
created_at TEXT NOT NULL
```

### `work_aliases`

Owner：`scholarly_catalog`。合併後的舊 identity 仍可解析；不得丟已寄歷史。

Migration：`0003`。欄位與約束：

```sql
alias_work_id TEXT NOT NULL PRIMARY KEY REFERENCES paper_works(id),
canonical_work_id TEXT NOT NULL REFERENCES paper_works(id),
decision_evidence_json TEXT NOT NULL CHECK(json_valid(decision_evidence_json)),
created_at TEXT NOT NULL,
CHECK(alias_work_id<>canonical_work_id)
```

### `paper_manifestations`

Owner：`scholarly_catalog`。預印本 repository 或正式出版呈現，不把 version 放在 work identity。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
work_id TEXT NOT NULL REFERENCES paper_works(id),
source_namespace TEXT NOT NULL,
native_id TEXT NOT NULL,
manifestation_kind TEXT NOT NULL CHECK(manifestation_kind IN ('preprint','publication','notice','repository_copy')),
landing_url TEXT NOT NULL,
created_at TEXT NOT NULL,
UNIQUE(source_namespace,native_id),
UNIQUE(id,work_id)
```

### `paper_revisions`

Owner：`scholarly_catalog`。一份 manifestation 的不可變內容修訂。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
manifestation_id TEXT NOT NULL,
work_id TEXT NOT NULL,
native_version TEXT,
content_fingerprint TEXT NOT NULL,
title TEXT NOT NULL,
abstract_object_id TEXT REFERENCES object_registry(object_id),
source_updated_at TEXT,
published_date TEXT,
date_precision TEXT CHECK(date_precision IN ('year','month','day','second')),
observed_at TEXT NOT NULL,
FOREIGN KEY(manifestation_id,work_id) REFERENCES paper_manifestations(id,work_id),
UNIQUE(manifestation_id,content_fingerprint),
UNIQUE(id,work_id)
```

### `external_identifiers`

Owner：`scholarly_catalog`。識別碼歸於呈現／書目 identity，不強制每 Work 一個 DOI。

Migration：`0003`。欄位與約束：

```sql
namespace TEXT NOT NULL,
normalized_value TEXT NOT NULL,
manifestation_id TEXT NOT NULL REFERENCES paper_manifestations(id),
source_evidence_id TEXT NOT NULL,
PRIMARY KEY(namespace,normalized_value)
```

### `work_relations`

Owner：`scholarly_catalog`。保留 relation type；correction 不等同 preprint-of。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
source_work_id TEXT NOT NULL REFERENCES paper_works(id),
target_work_id TEXT NOT NULL REFERENCES paper_works(id),
relation_type TEXT NOT NULL,
evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
observed_at TEXT NOT NULL,
CHECK(source_work_id<>target_work_id),
UNIQUE(source_work_id,target_work_id,relation_type)
```

### `catalog_field_provenance`

Owner：`scholarly_catalog`。展示欄位及衝突來源；原始觀測仍歸 Discovery。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
work_id TEXT NOT NULL REFERENCES paper_works(id),
field_name TEXT NOT NULL,
value_json TEXT NOT NULL CHECK(json_valid(value_json)),
source_observation_id TEXT NOT NULL,
preference_reason TEXT,
observed_at TEXT NOT NULL
```

### `access_assessments`

Owner：`scholarly_catalog`。免費閱讀、自動取得及用途各自保存證據和未知原因。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
manifestation_id TEXT NOT NULL REFERENCES paper_manifestations(id),
location_url TEXT NOT NULL,
reader_access TEXT NOT NULL CHECK(reader_access IN ('free','restricted','unknown')),
automated_retrieval TEXT NOT NULL CHECK(automated_retrieval IN ('permitted','prohibited','unknown')),
permitted_uses_json TEXT NOT NULL CHECK(json_valid(permitted_uses_json)),
license_id TEXT,
evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
checked_at TEXT NOT NULL,
expires_at TEXT,
content_version_binding TEXT
```

### `evidence_snapshots`

Owner：`scholarly_catalog`。本次實際讀取的文本與覆蓋範圍，不等於下載成功。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
revision_id TEXT NOT NULL,
work_id TEXT NOT NULL,
object_id TEXT NOT NULL REFERENCES object_registry(object_id),
text_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
parser_version TEXT NOT NULL,
evidence_level TEXT NOT NULL CHECK(evidence_level IN ('abstract_only','selected_sections','full_text')),
coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json)),
fingerprint TEXT NOT NULL UNIQUE,
created_at TEXT NOT NULL,
FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id),
UNIQUE(id,revision_id,work_id)
```

### `evidence_anchors`

Owner：`scholarly_catalog`。Python Unicode 字元 offset；表格 anchor 附 row/column label。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(id),
section_label TEXT,
paragraph_id TEXT,
quote TEXT NOT NULL CHECK(length(quote)>0),
offset_start INTEGER NOT NULL CHECK(offset_start>=0),
offset_end INTEGER NOT NULL CHECK(offset_end>offset_start),
table_locator_json TEXT CHECK(table_locator_json IS NULL OR json_valid(table_locator_json)),
UNIQUE(id,snapshot_id)
```

### `research_events`

Owner：`scholarly_catalog`。新文／補收錄／修訂／出版／更正／撤稿為不同事件。

Migration：`0003`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
work_id TEXT NOT NULL REFERENCES paper_works(id),
revision_id TEXT REFERENCES paper_revisions(id),
event_kind TEXT NOT NULL CHECK(event_kind IN ('new_work','late_discovery','revision_available','publication_status_changed','correction','retraction','newly_accessible','metadata_changed')),
canonical_event_key TEXT NOT NULL UNIQUE,
source_evidence_json TEXT NOT NULL CHECK(json_valid(source_evidence_json)),
occurred_at TEXT,
observed_at TEXT NOT NULL,
UNIQUE(id,work_id),
FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id)
```

### `source_bindings`

Owner：`discovery`。精確 query 與設定 identity；每個 binding 獨立進度。

Migration：`0004`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
profile_id TEXT NOT NULL,
profile_revision INTEGER NOT NULL,
source TEXT NOT NULL,
config_revision INTEGER NOT NULL,
compiled_query_json TEXT NOT NULL CHECK(json_valid(compiled_query_json)),
query_fingerprint TEXT NOT NULL,
enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
FOREIGN KEY(profile_id,profile_revision) REFERENCES watch_profile_revisions(profile_id,revision),
UNIQUE(profile_id,profile_revision,source,query_fingerprint)
```

### `harvest_units`

Owner：`discovery`。邏輯收集時間窗與 checkpoint；attempt 與工作不同。

Migration：`0004`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
binding_id TEXT NOT NULL REFERENCES source_bindings(id),
window_start TEXT NOT NULL,
window_end TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','verified_empty','partial','failed','unavailable')),
cursor_json TEXT CHECK(cursor_json IS NULL OR json_valid(cursor_json)),
checkpoint_version INTEGER NOT NULL DEFAULT 0,
coverage_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(coverage_json)),
created_at TEXT NOT NULL,
CHECK(window_start<window_end),
UNIQUE(binding_id,window_start,window_end)
```

### `harvest_attempts`

Owner：`discovery`。每次 source 呼叫／中斷有單獨收據。

Migration：`0004`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
unit_id TEXT NOT NULL REFERENCES harvest_units(id),
attempt_no INTEGER NOT NULL CHECK(attempt_no>0),
state TEXT NOT NULL,
error_code TEXT,
response_metadata_json TEXT CHECK(response_metadata_json IS NULL OR json_valid(response_metadata_json)),
started_at TEXT NOT NULL,
finished_at TEXT,
UNIQUE(unit_id,attempt_no)
```

### `source_observations`

Owner：`discovery`。raw payload 的唯一原始觀测 owner。

Migration：`0004`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
unit_id TEXT NOT NULL REFERENCES harvest_units(id),
source TEXT NOT NULL,
native_id TEXT NOT NULL,
payload_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
parser_version TEXT NOT NULL,
observed_at TEXT NOT NULL,
native_updated_at TEXT,
UNIQUE(unit_id,source,native_id,payload_object_id,parser_version)
```

### `provider_budgets`

Owner：`discovery`。provider 共用節流，不以領域分開避開總限制。

Migration：`0004`。欄位與約束：

```sql
source TEXT NOT NULL PRIMARY KEY,
next_allowed_at TEXT NOT NULL,
config_json TEXT NOT NULL CHECK(json_valid(config_json)),
updated_at TEXT NOT NULL
```

### `model_runs`

Owner：`paper_explanations`。模型、prompt、input、實際用量與失敗，不記 secrets。

Migration：`0005`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
task_kind TEXT NOT NULL,
provider TEXT NOT NULL,
model_name TEXT NOT NULL,
model_revision TEXT,
prompt_digest TEXT NOT NULL,
input_fingerprint TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('reserved','running','succeeded','failed','budget_blocked','stale')),
output_object_id TEXT REFERENCES object_registry(object_id),
input_tokens INTEGER CHECK(input_tokens>=0),
output_tokens INTEGER CHECK(output_tokens>=0),
actual_cost_micros INTEGER CHECK(actual_cost_micros>=0),
error_code TEXT,
started_at TEXT NOT NULL,
finished_at TEXT
```

### `usage_reservations`

Owner：`paper_explanations`。呼叫前保留預算，失敗也對帳實際使用。

Migration：`0005`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
run_id TEXT NOT NULL UNIQUE REFERENCES model_runs(id),
period_key TEXT NOT NULL,
currency TEXT NOT NULL,
reserved_micros INTEGER NOT NULL CHECK(reserved_micros>=0),
actual_micros INTEGER CHECK(actual_micros>=0),
state TEXT NOT NULL CHECK(state IN ('reserved','settled','released','unknown')),
created_at TEXT NOT NULL
```

### `paper_claims`

Owner：`paper_explanations`。作者報告的目的、方法、資料、結果與限制。

Migration：`0005`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(id),
extraction_run_id TEXT NOT NULL REFERENCES model_runs(id),
claim_type TEXT NOT NULL CHECK(claim_type IN ('objective','method','data','result','limitation','author_interpretation')),
claim_json TEXT NOT NULL CHECK(json_valid(claim_json)),
UNIQUE(id,snapshot_id)
```

### `claim_anchors`

Owner：`paper_explanations`。claim 與 anchor 必須出自同一 evidence snapshot。

Migration：`0005`。欄位與約束：

```sql
claim_id TEXT NOT NULL,
anchor_id TEXT NOT NULL,
snapshot_id TEXT NOT NULL,
PRIMARY KEY(claim_id,anchor_id),
FOREIGN KEY(claim_id,snapshot_id) REFERENCES paper_claims(id,snapshot_id),
FOREIGN KEY(anchor_id,snapshot_id) REFERENCES evidence_anchors(id,snapshot_id)
```

### `summary_revisions`

Owner：`paper_explanations`。輸出不可變，current pointer 與執行狀態分開。

Migration：`0005`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
revision_id TEXT NOT NULL,
work_id TEXT NOT NULL,
snapshot_id TEXT NOT NULL,
generation_fingerprint TEXT NOT NULL UNIQUE,
generation_run_id TEXT NOT NULL REFERENCES model_runs(id),
output_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
language TEXT NOT NULL,
explanation_profile TEXT NOT NULL,
qa_state TEXT NOT NULL CHECK(qa_state IN ('pending','passed','rejected')),
created_at TEXT NOT NULL,
FOREIGN KEY(snapshot_id,revision_id,work_id) REFERENCES evidence_snapshots(id,revision_id,work_id),
UNIQUE(id,revision_id,work_id),
UNIQUE(id,snapshot_id)
```

### `summary_claims`

Owner：`paper_explanations`。每個重要解說句對應抽取 claim。

Migration：`0005`。欄位與約束：

```sql
summary_id TEXT NOT NULL,
claim_id TEXT NOT NULL,
snapshot_id TEXT NOT NULL,
output_locator TEXT NOT NULL,
PRIMARY KEY(summary_id,claim_id,output_locator),
FOREIGN KEY(summary_id,snapshot_id) REFERENCES summary_revisions(id,snapshot_id),
FOREIGN KEY(claim_id,snapshot_id) REFERENCES paper_claims(id,snapshot_id)
```

### `verification_results`

Owner：`paper_explanations`。程式數值檢查與逐句 verifier 各有證據。

Migration：`0005`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
summary_id TEXT NOT NULL REFERENCES summary_revisions(id),
verifier_kind TEXT NOT NULL,
run_id TEXT REFERENCES model_runs(id),
report_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
verdict TEXT NOT NULL CHECK(verdict IN ('passed','rejected','failed')),
verified_at TEXT NOT NULL
```

### `current_summaries`

Owner：`paper_explanations`。發布必須以 CAS 核對當前 input fingerprint。

Migration：`0005`。欄位與約束：

```sql
work_id TEXT NOT NULL REFERENCES paper_works(id),
language TEXT NOT NULL,
explanation_profile TEXT NOT NULL,
summary_id TEXT NOT NULL,
revision_id TEXT NOT NULL,
expected_input_fingerprint TEXT NOT NULL,
pointer_version INTEGER NOT NULL CHECK(pointer_version>0),
PRIMARY KEY(work_id,language,explanation_profile),
FOREIGN KEY(summary_id,revision_id,work_id) REFERENCES summary_revisions(id,revision_id,work_id)
```

### `relevance_assessments`

Owner：`watch_profiles`。相關性及私人推薦理由，不改研究事實。

Migration：`0005`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
profile_id TEXT NOT NULL,
profile_revision INTEGER NOT NULL,
revision_id TEXT NOT NULL REFERENCES paper_revisions(id),
input_fingerprint TEXT NOT NULL,
decision TEXT CHECK(decision IN ('direct','adjacent','uncertain','irrelevant')),
execution_state TEXT NOT NULL CHECK(execution_state IN ('pending','succeeded','failed','stale')),
reason_json TEXT CHECK(reason_json IS NULL OR json_valid(reason_json)),
assessed_at TEXT NOT NULL,
FOREIGN KEY(profile_id,profile_revision) REFERENCES watch_profile_revisions(profile_id,revision),
CHECK(execution_state<>'succeeded' OR decision IS NOT NULL)
```

### `search_documents`

Owner：`retrieval`。對原始語意內容的檢索投影，不是 catalog authority。

Migration：`0006`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
work_id TEXT NOT NULL REFERENCES paper_works(id),
revision_id TEXT NOT NULL REFERENCES paper_revisions(id),
projection_kind TEXT NOT NULL,
text_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
input_fingerprint TEXT NOT NULL,
is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
sequence_no INTEGER NOT NULL UNIQUE,
UNIQUE(work_id,projection_kind,input_fingerprint),
FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id)
```

### `embedding_spaces`

Owner：`retrieval`。模型版本及處理方式決定向量空間，dimension 不寫死。

Migration：`0006`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
provider TEXT NOT NULL,
model_name TEXT NOT NULL,
model_revision TEXT NOT NULL,
dimension INTEGER NOT NULL CHECK(dimension>0),
dtype TEXT NOT NULL CHECK(dtype='float32'),
normalization_version TEXT NOT NULL,
prefix_config_hash TEXT NOT NULL,
metric TEXT NOT NULL CHECK(metric IN ('inner_product','l2')),
configuration_fingerprint TEXT NOT NULL UNIQUE,
created_at TEXT NOT NULL
```

### `embeddings`

Owner：`retrieval`。SQLite 配發穩定 int64 ID，原始向量另存不可變 npy。

Migration：`0006`。欄位與約束：

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT CHECK(id>0),
document_id TEXT NOT NULL REFERENCES search_documents(id),
space_id TEXT NOT NULL REFERENCES embedding_spaces(id),
object_id TEXT NOT NULL REFERENCES object_registry(object_id),
row_offset INTEGER NOT NULL CHECK(row_offset>=0),
input_fingerprint TEXT NOT NULL,
created_at TEXT NOT NULL,
UNIQUE(document_id,space_id,input_fingerprint),
UNIQUE(id,space_id)
```

### `index_generations`

Owner：`retrieval`。私有建置、校驗、發布的 FAISS generation。

Migration：`0006`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
space_id TEXT NOT NULL REFERENCES embedding_spaces(id),
state TEXT NOT NULL CHECK(state IN ('building','ready','failed')),
relative_directory TEXT NOT NULL UNIQUE,
manifest_sha256 TEXT,
index_sha256 TEXT,
membership_digest TEXT,
document_high_watermark INTEGER NOT NULL,
vector_count INTEGER NOT NULL CHECK(vector_count>=0),
created_at TEXT NOT NULL,
verified_at TEXT,
UNIQUE(id,space_id),
CHECK(state<>'ready' OR (manifest_sha256 IS NOT NULL AND index_sha256 IS NOT NULL AND membership_digest IS NOT NULL AND verified_at IS NOT NULL))
```

### `index_generation_members`

Owner：`retrieval`。同一 generation 只容許相同向量空間的成員。

Migration：`0006`。欄位與約束：

```sql
generation_id TEXT NOT NULL,
embedding_id INTEGER NOT NULL,
space_id TEXT NOT NULL,
PRIMARY KEY(generation_id,embedding_id),
FOREIGN KEY(generation_id,space_id) REFERENCES index_generations(id,space_id),
FOREIGN KEY(embedding_id,space_id) REFERENCES embeddings(id,space_id)
```

### `active_indexes`

Owner：`retrieval`。active 唯一權威在 SQLite，不另建 current.json。

Migration：`0006`。欄位與約束：

```sql
space_id TEXT NOT NULL PRIMARY KEY REFERENCES embedding_spaces(id),
generation_id TEXT NOT NULL,
pointer_version INTEGER NOT NULL CHECK(pointer_version>0),
activated_at TEXT NOT NULL,
FOREIGN KEY(generation_id,space_id) REFERENCES index_generations(id,space_id)
```

### `delivery_subscriptions`

Owner：`delivery`。寄送偏好獨立於研究範圍；secrets 不入表。

Migration：`0007`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
reader_id TEXT NOT NULL,
channel TEXT NOT NULL CHECK(channel IN ('email','rss')),
enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
timezone TEXT NOT NULL,
schedule_json TEXT NOT NULL CHECK(json_valid(schedule_json)),
max_items INTEGER NOT NULL CHECK(max_items>0),
recipient_ref TEXT NOT NULL,
policy_version INTEGER NOT NULL CHECK(policy_version>0),
created_at TEXT NOT NULL,
UNIQUE(reader_id,channel)
```

### `digests`

Owner：`delivery`。固定的每期內容與期間 identity，不在寄送時重新生成。

Migration：`0007`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
subscription_id TEXT NOT NULL REFERENCES delivery_subscriptions(id),
period_key TEXT NOT NULL,
cutoff_at TEXT NOT NULL,
rendered_object_id TEXT REFERENCES object_registry(object_id),
state TEXT NOT NULL CHECK(state IN ('prepared','queued','cancelled','sent','unknown')),
created_at TEXT NOT NULL,
UNIQUE(subscription_id,period_key)
```

### `digest_items`

Owner：`delivery`。卡片引用 exact summary revision；更正通知可沒有 summary。

Migration：`0007`。欄位與約束：

```sql
digest_id TEXT NOT NULL REFERENCES digests(id),
position INTEGER NOT NULL CHECK(position>0),
event_id TEXT NOT NULL REFERENCES research_events(id),
work_id TEXT NOT NULL REFERENCES paper_works(id),
summary_id TEXT,
revision_id TEXT,
item_kind TEXT NOT NULL CHECK(item_kind IN ('paper','status_notice')),
PRIMARY KEY(digest_id,position),
UNIQUE(digest_id,event_id),
FOREIGN KEY(summary_id,revision_id,work_id) REFERENCES summary_revisions(id,revision_id,work_id),
CHECK((item_kind='paper' AND summary_id IS NOT NULL AND revision_id IS NOT NULL) OR item_kind='status_notice'),
FOREIGN KEY(event_id,work_id) REFERENCES research_events(id,work_id),
CHECK((summary_id IS NULL AND revision_id IS NULL) OR (summary_id IS NOT NULL AND revision_id IS NOT NULL))
```

### `delivery_outbox`

Owner：`delivery`。每個 digest 一筆 durable request；不保證 SMTP exactly once。

Migration：`0007`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
digest_id TEXT NOT NULL UNIQUE REFERENCES digests(id),
idempotency_key TEXT NOT NULL UNIQUE,
payload_sha256 TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('pending','sending','provider_accepted','failed','unknown','cancelled')),
workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch>0),
next_attempt_at TEXT,
created_at TEXT NOT NULL
```

### `notification_ledger`

Owner：`delivery`。讀者×研究事件×channel 只一個通知身份。

Migration：`0007`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
reader_id TEXT NOT NULL,
event_id TEXT NOT NULL REFERENCES research_events(id),
channel TEXT NOT NULL,
outbox_id TEXT NOT NULL REFERENCES delivery_outbox(id),
state TEXT NOT NULL CHECK(state IN ('reserved','accepted','unknown','cancelled')),
created_at TEXT NOT NULL,
UNIQUE(reader_id,event_id,channel)
```

### `delivery_attempts`

Owner：`delivery`。request／attempt／provider accepted／read 分開。

Migration：`0007`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
outbox_id TEXT NOT NULL REFERENCES delivery_outbox(id),
attempt_no INTEGER NOT NULL CHECK(attempt_no>0),
state TEXT NOT NULL CHECK(state IN ('sending','provider_accepted','failed','unknown')),
provider_message_id TEXT,
error_code TEXT,
started_at TEXT NOT NULL,
finished_at TEXT,
UNIQUE(outbox_id,attempt_no)
```

### `reader_feedback`

Owner：`delivery`。私人使用者偏好事件，不回寫為學術品質。

Migration：`0007`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
reader_id TEXT NOT NULL,
work_id TEXT NOT NULL REFERENCES paper_works(id),
action TEXT NOT NULL CHECK(action IN ('useful','relevant_not_urgent','irrelevant','read','saved','unsaved')),
profile_id TEXT REFERENCES watch_profiles(id),
created_at TEXT NOT NULL
```

### `workflow_jobs`

Owner：`research_workflow`。應用排程與持久工作，單一 job 可有多個 attempts。

Migration：`0008`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
job_kind TEXT NOT NULL,
business_key TEXT NOT NULL UNIQUE,
input_json TEXT NOT NULL CHECK(json_valid(input_json)),
input_fingerprint TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','cancelled','budget_blocked','awaiting_external')),
due_at TEXT NOT NULL,
lease_owner TEXT,
lease_until TEXT,
fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token>=0),
attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
created_at TEXT NOT NULL
```

### `job_attempts`

Owner：`research_workflow`。execution 收據不可當 acceptance。

Migration：`0008`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
job_id TEXT NOT NULL REFERENCES workflow_jobs(id),
attempt_no INTEGER NOT NULL CHECK(attempt_no>0),
fencing_token INTEGER NOT NULL,
state TEXT NOT NULL,
error_code TEXT,
started_at TEXT NOT NULL,
finished_at TEXT,
UNIQUE(job_id,attempt_no)
```

### `workflow_events`

Owner：`research_workflow`。短交易記錄下游待辦，避免跨 BC 寫入後漏排程。

Migration：`0008`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
source_kind TEXT NOT NULL,
source_identity TEXT NOT NULL,
event_kind TEXT NOT NULL,
business_key TEXT NOT NULL UNIQUE,
payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
consumed_at TEXT,
created_at TEXT NOT NULL
```

### `backup_runs`

Owner：`research_workflow`。備份 manifest 的識別，不把可重建索引當唯一原始資料。

Migration：`0008`。欄位與約束：

```sql
id TEXT NOT NULL PRIMARY KEY,
workspace_id TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('building','verified','failed')),
relative_directory TEXT NOT NULL,
database_sha256 TEXT,
manifest_sha256 TEXT,
object_count INTEGER CHECK(object_count>=0),
created_at TEXT NOT NULL,
verified_at TEXT
```

## 七、schema 草案的明確限制

schema 通過解析／外鍵測試不代表 SQL adapter 或業務 invariant 已完成。WorkAlias 不成環、source-observation provenance 指向有效來源、digest 只使用 QA/current 資料、index membership count 和所存檔案相符、配置 revision 指紋一致，均需 owning Task 的 application/contract 驗收。不得把這些未實作條件寫成 production PASS。


## Task #7：本機儲存 adapter 契約

本段補足 schema 草案的執行契約，不改 0001 SQL 內容。SQLite application_id 為 `0x50524452`；現有非本產品 DB 拒絕採用。缺 DB 且留有物件或不明 state 檔案時拒絕重新初始化，以免用新 identity 隱藏遺失的帳本。

ObjectRef 以 `kind:sha256` 為 object_id，relative_path 為 `objects/<kind>/<sha256[:2]>/<sha256>`；副檔名不參與身份，媒體型別存 registry。相同 kind/hash 若媒體或保留政策不同，回 metadata_conflict；內容變更必須得到不同 hash，不覆寫既有物件。檔案採同檔案系統暫存、完整 fsync、不覆蓋式 hard-link 發布與父目錄 fsync；之後才提交 registry。SQL 失敗留下完整未登錄內容，重跑可以接回。缺失／損毀不自動回填或改狀態，保留診斷及復原決策邊界。

本批目標為單一使用者管理的本機 POSIX filesystem（Linux／macOS），要求 hard links、目錄 fsync 與 SQLite WAL 所需能力；平台證據以 exact candidate CI 為準。不宣稱支援 Windows、網路檔案系統、雲端同步工作區或不可靠 fsync 硬體。不將 SQLite／FS 描述為跨資源單一交易。靜態 root／managed path／sidecar symlink 被拒絕；可信 OS 祖先如 macOS /var 可解析。這不是對可同時修改 workspace 的惡意同 UID 程序提供完整 TOCTOU 隔離，工作區必須由同一使用者受控管理。

InspectStorage 為唯讀觀測：registry snapshot 與 FS 掃描並非同一時刻，並行發布可能暫時顯示 unregistered；禁止據此直接 GC。ReadObject 依預期長度限制讀取，bytes 與 SHA256 不符即拒絕；不把缺資料當空內容。應用只透過 registry／bytes／unit-of-work ports 使用能力，不任意跨 BC 寫表。備份／正式復原及 ledger 保護仍由後續工作驗證。
