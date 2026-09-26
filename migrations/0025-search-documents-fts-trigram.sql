-- Owner: retrieval. Append-only derived FTS5 trigram projection.
-- Do not edit applied 0006; both FTS tables are rebuildable from current search_documents.
CREATE VIRTUAL TABLE search_documents_fts_trigram
USING fts5(
  document_id UNINDEXED,
  title,
  abstract,
  explanation,
  tokenize='trigram'
);
