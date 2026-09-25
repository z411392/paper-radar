import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.adapters.driven.sqlite_pubmed_observation_replay_adapter import (
    SqlitePubmedObservationReplayAdapter,
)
from libs.discovery.exceptions.pubmed_observation_replay_error import (
    PubmedObservationReplayError,
)
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
OBSERVED = "2026-09-25T11:59:00+00:00"
UNIT = "unit:pubmed-replay"
PMID = "100"
PARSER = PubmedSourceAdapter.PARSER_VERSION


def _body() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>100</PMID>
      <Article>
        <ArticleTitle>Reliable PubMed Systems</ArticleTitle>
        <Abstract><AbstractText>PubMed abstract.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Example</LastName><ForeName>Alice</ForeName></Author>
        </AuthorList>
        <Journal>
          <JournalIssue>
            <PubDate><Year>2026</Year><Month>Sep</Month><Day>20</Day></PubDate>
          </JournalIssue>
          <Title>Journal of Reliable Systems</Title>
        </Journal>
        <Language>eng</Language>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">100</ArticleId>
        <ArticleId IdType="doi">10.1234/Example</ArticleId>
        <ArticleId IdType="pmc">PMC12345</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


def _canonical_pmids(values: list[str]) -> str:
    return json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _setup(tmp_path: Path):
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=10)
    body = _body()
    digest = hashlib.sha256(body).hexdigest()
    object_id = "raw:" + digest
    search_sha = "d" * 64
    search_object = "raw:" + search_sha
    pmids_json = _canonical_pmids([PMID])
    observation_id = "observation:" + hashlib.sha256(
        _canonical_pmids([UNIT, "pubmed", PMID, object_id, PARSER]).encode("utf-8")
    ).hexdigest()

    connection = schema.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
            ("personal", "reader:local", "profile", "active", None, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
            (
                "personal",
                1,
                "scope",
                '{"sources":["pubmed"]}',
                "a" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "UPDATE watch_profiles SET published_revision=1 WHERE id='personal'"
        )
        connection.execute(
            "INSERT INTO source_bindings VALUES(?,?,?,?,?,?,?,?)",
            (
                "binding:pubmed-replay",
                "personal",
                1,
                "pubmed",
                1,
                "{}",
                "b" * 64,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO harvest_units VALUES(?,?,?,?,?,?,?,?,?)",
            (
                UNIT,
                "binding:pubmed-replay",
                "2026-09-24T00:00:00+00:00",
                "2026-09-25T00:00:00+00:00",
                "succeeded",
                '{"format_version":1,"next_start":1,"total_results":1}',
                1,
                '{"complete":true,"format_version":1,"record_count":1}',
                NOW.isoformat(),
            ),
        )
        for oid, sha, size in (
            (object_id, digest, len(body)),
            (search_object, search_sha, 1),
        ):
            connection.execute(
                "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    oid,
                    sha,
                    "objects/raw/" + sha,
                    "raw",
                    "application/octet-stream",
                    size,
                    "available",
                    NOW.isoformat(),
                    "source-response",
                ),
            )
        connection.execute(
            "INSERT INTO pubmed_harvest_pages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                UNIT,
                0,
                1,
                pmids_json,
                "page:" + "e" * 59,
                search_object,
                search_sha,
                "complete",
                1,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO pubmed_bibliography_batches VALUES(?,?,?,?,?,?,?,?)",
            (
                UNIT,
                0,
                0,
                pmids_json,
                object_id,
                digest,
                PARSER,
                OBSERVED,
            ),
        )
        connection.execute(
            "INSERT INTO source_observations VALUES(?,?,?,?,?,?,?,NULL)",
            (
                observation_id,
                UNIT,
                "pubmed",
                PMID,
                object_id,
                PARSER,
                OBSERVED,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return schema, body, object_id, observation_id


def test_exact_pubmed_observation_replays_bibliography(tmp_path: Path) -> None:
    schema, body, object_id, observation_id = _setup(tmp_path)
    adapter = SqlitePubmedObservationReplayAdapter(
        schema.connect,
        lambda requested: body if requested == object_id else b"",
    )

    replay = adapter(observation_id)

    assert replay.observation_id == observation_id
    assert replay.record.pmid == PMID
    assert replay.record.title == "Reliable PubMed Systems"
    assert replay.record.abstract == "PubMed abstract."
    assert replay.record.doi == "10.1234/example"
    assert replay.record.pmcid == "PMC12345"
    assert replay.record.publication_date == "2026-Sep-20"
    assert replay.record.publication_date_precision == "day"


def test_pubmed_parser_version_drift_is_rejected(tmp_path: Path) -> None:
    schema, body, _, observation_id = _setup(tmp_path)
    connection = schema.connect()
    try:
        connection.execute(
            "UPDATE source_observations SET parser_version='pubmed-parser-v999'"
        )
        connection.commit()
    finally:
        connection.close()

    adapter = SqlitePubmedObservationReplayAdapter(schema.connect, lambda _: body)
    with pytest.raises(
        PubmedObservationReplayError,
        match="pubmed_observation_parser_version_mismatch",
    ):
        adapter(observation_id)


def test_pubmed_observation_with_two_matching_batches_is_ambiguous(
    tmp_path: Path,
) -> None:
    schema, body, object_id, observation_id = _setup(tmp_path)
    connection = schema.connect()
    try:
        connection.execute(
            "INSERT INTO pubmed_harvest_pages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                UNIT,
                1,
                2,
                _canonical_pmids([PMID]),
                "page:" + "f" * 59,
                "raw:" + "d" * 64,
                "d" * 64,
                "complete",
                1,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO pubmed_bibliography_batches VALUES(?,?,?,?,?,?,?,?)",
            (
                UNIT,
                1,
                0,
                _canonical_pmids([PMID]),
                object_id,
                hashlib.sha256(body).hexdigest(),
                PARSER,
                OBSERVED,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    adapter = SqlitePubmedObservationReplayAdapter(schema.connect, lambda _: body)
    with pytest.raises(
        PubmedObservationReplayError,
        match="pubmed_observation_batch_ambiguous",
    ):
        adapter(observation_id)
