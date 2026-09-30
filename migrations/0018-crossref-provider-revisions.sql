-- Crossref provider metadata identity/revision layer.
-- Owner: scholarly_catalog. Provider durability is not paper-revision success.
CREATE TABLE crossref_source_records (
  canonical_doi TEXT PRIMARY KEY NOT NULL,
  first_raw_doi TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);

CREATE TABLE crossref_provider_revisions (
  id TEXT PRIMARY KEY NOT NULL,
  canonical_doi TEXT NOT NULL REFERENCES crossref_source_records(canonical_doi),
  revision_no INTEGER NOT NULL CHECK(typeof(revision_no)='integer' AND revision_no>0),
  raw_doi TEXT NOT NULL,
  provider_sha256 TEXT NOT NULL CHECK(length(provider_sha256)=64
    AND provider_sha256 NOT GLOB '*[^0-9a-f]*'),
  semantic_sha256 TEXT NOT NULL CHECK(length(semantic_sha256)=64
    AND semantic_sha256 NOT GLOB '*[^0-9a-f]*'),
  semantic_version TEXT NOT NULL,
  canonical_json TEXT NOT NULL CHECK(json_valid(canonical_json)),
  title TEXT,
  indexed_at TEXT,
  created_at TEXT,
  deposited_at TEXT,
  published_date TEXT,
  published_precision TEXT CHECK(
    published_precision IS NULL OR published_precision IN ('year','month','day')
  ),
  parse_warnings_json TEXT NOT NULL CHECK(json_valid(parse_warnings_json)),
  first_observed_at TEXT NOT NULL,
  UNIQUE(canonical_doi,revision_no),
  UNIQUE(canonical_doi,provider_sha256)
);

CREATE TABLE crossref_provider_observations (
  id TEXT PRIMARY KEY NOT NULL,
  provider_revision_id TEXT NOT NULL REFERENCES crossref_provider_revisions(id),
  canonical_doi TEXT NOT NULL REFERENCES crossref_source_records(canonical_doi),
  page_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>=0),
  observed_at TEXT NOT NULL,
  UNIQUE(page_id,ordinal)
);

CREATE TRIGGER crossref_source_record_immutable
BEFORE UPDATE ON crossref_source_records
BEGIN SELECT RAISE(ABORT,'crossref_source_record_immutable'); END;
CREATE TRIGGER crossref_source_record_keep_history
BEFORE DELETE ON crossref_source_records
BEGIN SELECT RAISE(ABORT,'crossref_source_record_history_required'); END;

CREATE TRIGGER crossref_provider_revision_immutable
BEFORE UPDATE ON crossref_provider_revisions
BEGIN SELECT RAISE(ABORT,'crossref_provider_revision_immutable'); END;
CREATE TRIGGER crossref_provider_revision_keep_history
BEFORE DELETE ON crossref_provider_revisions
BEGIN SELECT RAISE(ABORT,'crossref_provider_revision_history_required'); END;

CREATE TRIGGER crossref_provider_observation_immutable
BEFORE UPDATE ON crossref_provider_observations
BEGIN SELECT RAISE(ABORT,'crossref_provider_observation_immutable'); END;
CREATE TRIGGER crossref_provider_observation_keep_history
BEFORE DELETE ON crossref_provider_observations
BEGIN SELECT RAISE(ABORT,'crossref_provider_observation_history_required'); END;
