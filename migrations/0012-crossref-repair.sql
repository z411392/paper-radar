-- Append-only Crossref repair/finalization state.
-- Owner: discovery. Historical traversals remain in 0011; this layer derives repair and watermark state.

CREATE TABLE crossref_streams (
id TEXT NOT NULL PRIMARY KEY,
binding_key TEXT NOT NULL,
config_version TEXT NOT NULL,
rows INTEGER NOT NULL CHECK(rows BETWEEN 1 AND 1000),
scope_sha256 TEXT NOT NULL CHECK(length(scope_sha256)=64 AND scope_sha256 NOT GLOB '*[^0-9a-f]*'),
created_at TEXT NOT NULL,
UNIQUE(binding_key,config_version,rows)
);

CREATE TABLE crossref_repair_runs (
id TEXT NOT NULL PRIMARY KEY,
window_id TEXT NOT NULL REFERENCES crossref_harvest_windows(id),
repair_no INTEGER NOT NULL CHECK(repair_no>0),
pass_id TEXT NOT NULL UNIQUE REFERENCES crossref_harvest_passes(id),
reason TEXT NOT NULL,
state TEXT NOT NULL CHECK(state IN ('running','completed','failed')),
created_at TEXT NOT NULL,
finished_at TEXT,
error_code TEXT,
UNIQUE(window_id,repair_no)
);

CREATE TABLE crossref_window_finalizations (
id TEXT NOT NULL PRIMARY KEY,
window_id TEXT NOT NULL REFERENCES crossref_harvest_windows(id),
stream_id TEXT NOT NULL REFERENCES crossref_streams(id),
generation INTEGER NOT NULL CHECK(generation>0),
pass_id TEXT NOT NULL UNIQUE REFERENCES crossref_harvest_passes(id),
reason TEXT NOT NULL,
finalized_at TEXT NOT NULL,
UNIQUE(window_id,generation)
);

CREATE TABLE crossref_binding_watermarks (
stream_id TEXT NOT NULL PRIMARY KEY REFERENCES crossref_streams(id),
binding_key TEXT NOT NULL,
config_version TEXT NOT NULL,
rows INTEGER NOT NULL CHECK(rows BETWEEN 1 AND 1000),
finalized_until TEXT NOT NULL,
latest_window_id TEXT NOT NULL REFERENCES crossref_harvest_windows(id),
latest_finalization_id TEXT NOT NULL REFERENCES crossref_window_finalizations(id),
updated_at TEXT NOT NULL
);

CREATE INDEX idx_crossref_repairs_window
ON crossref_repair_runs(window_id,repair_no);

CREATE INDEX idx_crossref_finalizations_stream
ON crossref_window_finalizations(stream_id,window_id,generation);
