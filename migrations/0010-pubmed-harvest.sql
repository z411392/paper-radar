-- Append-only PubMed two-stage harvest receipts.
-- Owner: discovery. ESearch candidate IDs are not source observations until EFetch bibliography is durable.

CREATE TABLE pubmed_harvest_pages (
unit_id TEXT NOT NULL REFERENCES harvest_units(id),
start_index INTEGER NOT NULL CHECK(start_index>=0),
total_results INTEGER NOT NULL CHECK(total_results>=0),
pmids_json TEXT NOT NULL CHECK(json_valid(pmids_json)),
page_fingerprint TEXT NOT NULL,
search_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
search_sha256 TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('searched','bibliography_partial','complete')),
next_batch_offset INTEGER NOT NULL DEFAULT 0 CHECK(next_batch_offset>=0),
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
PRIMARY KEY(unit_id,start_index),
UNIQUE(unit_id,page_fingerprint)
);

CREATE TABLE pubmed_bibliography_batches (
unit_id TEXT NOT NULL,
start_index INTEGER NOT NULL,
batch_offset INTEGER NOT NULL CHECK(batch_offset>=0),
pmids_json TEXT NOT NULL CHECK(json_valid(pmids_json)),
payload_object_id TEXT NOT NULL REFERENCES object_registry(object_id),
response_sha256 TEXT NOT NULL,
parser_version TEXT NOT NULL,
observed_at TEXT NOT NULL,
PRIMARY KEY(unit_id,start_index,batch_offset),
FOREIGN KEY(unit_id,start_index) REFERENCES pubmed_harvest_pages(unit_id,start_index)
);
