-- Append-only Crossref bounded-window traversal journal.
-- Owner: discovery. Cursor is a per-pass page hint, never the global watermark.

CREATE TABLE crossref_harvest_windows (
id TEXT NOT NULL PRIMARY KEY,
binding_key TEXT NOT NULL,
query_fingerprint TEXT NOT NULL,
config_version TEXT NOT NULL,
from_index TEXT NOT NULL,
until_index TEXT NOT NULL,
rows INTEGER NOT NULL CHECK(rows BETWEEN 1 AND 1000),
state TEXT NOT NULL CHECK(state IN ('pending','running','traversed','repair_pending','failed')),
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
UNIQUE(binding_key,query_fingerprint),
CHECK(from_index<until_index)
);

CREATE TABLE crossref_harvest_passes (
id TEXT NOT NULL PRIMARY KEY,
window_id TEXT NOT NULL REFERENCES crossref_harvest_windows(id),
pass_no INTEGER NOT NULL CHECK(pass_no>0),
parameters_fingerprint TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('running','completed','failed')),
current_cursor TEXT,
next_page_no INTEGER NOT NULL DEFAULT 0 CHECK(next_page_no>=0),
traversal_complete INTEGER NOT NULL DEFAULT 0 CHECK(traversal_complete IN (0,1)),
accounting_complete INTEGER NOT NULL DEFAULT 0 CHECK(accounting_complete IN (0,1)),
first_reported_total INTEGER CHECK(first_reported_total IS NULL OR first_reported_total>=0),
raw_item_count INTEGER NOT NULL DEFAULT 0 CHECK(raw_item_count>=0),
processed_item_count INTEGER NOT NULL DEFAULT 0 CHECK(processed_item_count>=0),
quarantine_count INTEGER NOT NULL DEFAULT 0 CHECK(quarantine_count>=0),
unique_doi_count INTEGER NOT NULL DEFAULT 0 CHECK(unique_doi_count>=0),
duplicate_doi_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_doi_count>=0),
parse_gap_count INTEGER NOT NULL DEFAULT 0 CHECK(parse_gap_count>=0),
drift_suspected INTEGER NOT NULL DEFAULT 0 CHECK(drift_suspected IN (0,1)),
repair_pending INTEGER NOT NULL DEFAULT 0 CHECK(repair_pending IN (0,1)),
source_completeness TEXT NOT NULL DEFAULT 'unknown'
  CHECK(source_completeness IN ('unknown','provisional','complete')),
error_code TEXT,
started_at TEXT NOT NULL,
finished_at TEXT,
UNIQUE(window_id,pass_no)
);

CREATE TABLE crossref_harvest_pages (
id TEXT NOT NULL PRIMARY KEY,
pass_id TEXT NOT NULL REFERENCES crossref_harvest_passes(id),
page_no INTEGER NOT NULL CHECK(page_no>=0),
cursor_in TEXT NOT NULL,
cursor_out TEXT,
request_fingerprint TEXT NOT NULL,
successful_receipt_id TEXT REFERENCES object_registry(object_id),
content_coded_sha256 TEXT,
entity_sha256 TEXT,
parser_version TEXT,
reported_total INTEGER CHECK(reported_total IS NULL OR reported_total>=0),
item_count INTEGER CHECK(item_count IS NULL OR item_count>=0),
traversal_end_hint INTEGER CHECK(traversal_end_hint IS NULL OR traversal_end_hint IN (0,1)),
state TEXT NOT NULL CHECK(state IN ('requested','captured','decoded','accounted','failed')),
last_error_code TEXT,
created_at TEXT NOT NULL,
decoded_at TEXT,
committed_at TEXT,
UNIQUE(pass_id,page_no),
UNIQUE(pass_id,request_fingerprint),
UNIQUE(successful_receipt_id)
);

CREATE TABLE crossref_harvest_page_attempts (
id TEXT NOT NULL PRIMARY KEY,
page_id TEXT NOT NULL REFERENCES crossref_harvest_pages(id),
attempt_no INTEGER NOT NULL CHECK(attempt_no>0),
receipt_id TEXT NOT NULL UNIQUE REFERENCES object_registry(object_id),
action TEXT NOT NULL CHECK(action IN ('accept','retry','stop')),
failure_code TEXT,
recorded_at TEXT NOT NULL,
UNIQUE(page_id,attempt_no),
CHECK(
  (action='accept' AND failure_code IS NULL)
  OR (action IN ('retry','stop') AND failure_code IS NOT NULL)
)
);

CREATE TABLE crossref_harvest_items (
page_id TEXT NOT NULL REFERENCES crossref_harvest_pages(id),
ordinal INTEGER NOT NULL CHECK(ordinal>=0),
raw_doi TEXT,
canonical_json TEXT NOT NULL CHECK(json_valid(canonical_json)),
canonical_sha256 TEXT NOT NULL,
decode_state TEXT NOT NULL CHECK(decode_state IN ('decoded','quarantined')),
outcome_state TEXT NOT NULL CHECK(outcome_state IN ('pending','processed','quarantined')),
canonical_doi TEXT,
outcome_ref TEXT,
error_code TEXT,
created_at TEXT NOT NULL,
processed_at TEXT,
PRIMARY KEY(page_id,ordinal),
CHECK(
  (decode_state='decoded' AND outcome_state IN ('pending','processed'))
  OR (decode_state='quarantined' AND outcome_state='quarantined')
),
CHECK(
  (outcome_state='processed' AND canonical_doi IS NOT NULL AND outcome_ref IS NOT NULL)
  OR (outcome_state<>'processed' AND canonical_doi IS NULL AND outcome_ref IS NULL)
)
);

CREATE INDEX idx_crossref_pass_window ON crossref_harvest_passes(window_id,pass_no);
CREATE INDEX idx_crossref_page_pass ON crossref_harvest_pages(pass_id,page_no);
CREATE INDEX idx_crossref_item_outcome ON crossref_harvest_items(page_id,outcome_state);
