-- Append-only runtime migration: domain-aware relevance identity for durable digest selection.
-- Owner: watch_profiles. Do not edit prior 0005 relevance_assessments in place.

CREATE TABLE relevance_assessment_domains (
assessment_id TEXT NOT NULL PRIMARY KEY REFERENCES relevance_assessments(id),
domain_id TEXT NOT NULL,
domain_revision INTEGER NOT NULL CHECK(domain_revision>0),
snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(id),
FOREIGN KEY(domain_id,domain_revision) REFERENCES domain_definitions(id,revision)
);

CREATE INDEX idx_relevance_assessment_domains_lookup
ON relevance_assessment_domains(domain_id,domain_revision,snapshot_id);
