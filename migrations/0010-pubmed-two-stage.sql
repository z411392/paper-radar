-- Append-only runtime migration for durable PubMed ESearch -> EFetch checkpoints.
-- Owner: discovery. Do not edit 0004 in place.

CREATE TABLE pubmed_window_progress (
business_key TEXT NOT NULL PRIMARY KEY,
query_fingerprint TEXT NOT NULL,
window_start TEXT NOT NULL,
window_end TEXT NOT NULL,
next_start INTEGER NOT NULL DEFAULT 0 CHECK(next_start>=0),
total_results INTEGER CHECK(total_results IS NULL OR total_results>=0),
state TEXT NOT NULL CHECK(state IN ('pending','succeeded','verified_empty')),
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
CHECK(window_start<window_end),
CHECK(total_results IS NULL OR next_start<=total_results)
);

CREATE TABLE pubmed_search_pages (
id TEXT NOT NULL PRIMARY KEY,
business_key TEXT NOT NULL REFERENCES pubmed_window_progress(business_key),
start_index INTEGER NOT NULL CHECK(start_index>=0),
pmids_json TEXT NOT NULL CHECK(json_valid(pmids_json)),
raw_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
request_fingerprint TEXT NOT NULL,
response_sha256 TEXT NOT NULL,
observed_at TEXT NOT NULL,
UNIQUE(business_key,start_index)
);

CREATE TABLE pubmed_bibliography_observations (
id TEXT NOT NULL PRIMARY KEY,
business_key TEXT NOT NULL REFERENCES pubmed_window_progress(business_key),
start_index INTEGER NOT NULL CHECK(start_index>=0),
pmid TEXT NOT NULL,
raw_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
request_fingerprint TEXT NOT NULL,
response_sha256 TEXT NOT NULL,
parser_version TEXT NOT NULL,
record_json TEXT NOT NULL CHECK(json_valid(record_json)),
content_fingerprint TEXT NOT NULL,
observed_at TEXT NOT NULL,
UNIQUE(business_key,pmid)
);

CREATE INDEX idx_pubmed_bibliography_window_start
ON pubmed_bibliography_observations(business_key,start_index);
