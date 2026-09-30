import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import PARSER_VERSION
from libs.discovery.adapters.driven.sqlite_arxiv_observation_replay_adapter import (
    SqliteArxivObservationReplayAdapter,
)
from libs.discovery.domain.services.harvest_page_rules import HarvestPageRules
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.arxiv_observation_replay_error import (
    ArxivObservationReplayError,
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
OBSERVED = "2026-09-25T11:59:00.000000+00:00"
QUERY = "a" * 64
REQUEST = "b" * 64
UNIT = "unit:arxiv-replay"
OBSERVATION = "observation:" + "c" * 64


def _body(version: int = 1) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <id>https://export.arxiv.org/api/query</id>
  <updated>2026-09-25T11:58:00Z</updated>
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <entry>
    <id>https://arxiv.org/abs/2501.12345v{version}</id>
    <updated>2026-09-25T11:00:00Z</updated>
    <published>2026-09-24T10:00:00Z</published>
    <title>Reliable Systems</title>
    <summary>An abstract.</summary>
    <author><name>Alice Example</name></author>
    <category term="cs.SE" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:primary_category term="cs.SE"/>
  </entry>
</feed>""".encode()


def _setup(tmp_path: Path):
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    schema = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=4,
    )
    request = SourcePageRequest(
        "arxiv",
        QUERY,
        REQUEST,
        "GET",
        "https://export.arxiv.org/api/query?fixture=1",
        0,
        1,
        10,
    )
    body = _body()
    digest = hashlib.sha256(body).hexdigest()
    object_id = "raw:" + digest
    capture = {
        "format_version": 1,
        "request_fingerprint": REQUEST,
        "raw_object_id": object_id,
        "response_sha256": digest,
        "failure_code": None,
        "retryable": False,
        "retry_after_seconds": None,
        "status": 200,
        "headers": [["content-type", "application/atom+xml"]],
        "received_at": OBSERVED,
        "byte_size": len(body),
        "body_complete": True,
        "capture_error": None,
        "hash_scope": "complete_body",
    }
    envelope = HarvestPageRules.encode(
        {
            "format_version": 1,
            "request": asdict(request),
            "capture": capture,
        }
    )

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
                '{"sources":["arxiv"]}',
                "d" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "UPDATE watch_profiles SET published_revision=1 WHERE id='personal'"
        )
        connection.execute(
            "INSERT INTO source_bindings VALUES(?,?,?,?,?,?,?,?)",
            (
                "binding:arxiv-replay",
                "personal",
                1,
                "arxiv",
                1,
                "{}",
                QUERY,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO harvest_units VALUES(?,?,?,?,?,?,?,?,?)",
            (
                UNIT,
                "binding:arxiv-replay",
                "2026-09-24T00:00:00+00:00",
                "2026-09-25T00:00:00+00:00",
                "succeeded",
                '{"format_version":1,"next_start":1,"total_results":1}',
                1,
                '{"complete":true,"format_version":1,"record_count":1}',
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
            (
                object_id,
                digest,
                "objects/raw/" + digest,
                "raw",
                "application/atom+xml",
                len(body),
                "available",
                NOW.isoformat(),
                "source-response",
            ),
        )
        connection.execute(
            "INSERT INTO harvest_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "attempt:arxiv-replay",
                UNIT,
                1,
                "captured",
                None,
                envelope,
                "2026-09-25T11:58:30+00:00",
                OBSERVED,
            ),
        )
        connection.execute(
            "INSERT INTO source_observations VALUES(?,?,?,?,?,?,?,?)",
            (
                OBSERVATION,
                UNIT,
                "arxiv",
                "2501.12345v1",
                object_id,
                PARSER_VERSION,
                OBSERVED,
                "2026-09-25T11:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return schema, request, body, object_id


def test_exact_observation_replays_from_durable_raw(tmp_path: Path) -> None:
    schema, _, body, object_id = _setup(tmp_path)
    adapter = SqliteArxivObservationReplayAdapter(
        schema.connect,
        lambda requested: body if requested == object_id else b"",
    )

    replay = adapter(OBSERVATION)

    assert replay.observation_id == OBSERVATION
    assert replay.unit_id == UNIT
    assert replay.payload_object_id == object_id
    assert replay.parser_version == PARSER_VERSION
    assert replay.record.source_record_id == "2501.12345v1"
    assert replay.record.arxiv_id == "2501.12345"
    assert replay.record.version == 1
    assert replay.record.abstract == "An abstract."


def test_parser_version_drift_is_rejected(tmp_path: Path) -> None:
    schema, _, body, _ = _setup(tmp_path)
    connection = schema.connect()
    try:
        connection.execute(
            "UPDATE source_observations SET parser_version='arxiv-atom-v999'"
        )
        connection.commit()
    finally:
        connection.close()

    adapter = SqliteArxivObservationReplayAdapter(schema.connect, lambda _: body)
    with pytest.raises(
        ArxivObservationReplayError,
        match="arxiv_observation_parser_version_mismatch",
    ):
        adapter(OBSERVATION)


def test_same_raw_with_inconsistent_request_is_ambiguous(tmp_path: Path) -> None:
    schema, request, body, object_id = _setup(tmp_path)
    connection = schema.connect()
    try:
        other = SourcePageRequest(
            request.source_id,
            request.query_fingerprint,
            "e" * 64,
            request.method,
            request.url + "&other=1",
            request.start,
            request.max_results,
            request.maximum_window_results,
        )
        envelope = connection.execute(
            "SELECT response_metadata_json FROM harvest_attempts"
        ).fetchone()[0]
        decoded = HarvestPageRules.decode(envelope)
        decoded["request"] = asdict(other)
        decoded["capture"]["request_fingerprint"] = other.request_fingerprint
        connection.execute(
            "INSERT INTO harvest_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "attempt:arxiv-conflict",
                UNIT,
                2,
                "captured",
                None,
                HarvestPageRules.encode(decoded),
                "2026-09-25T11:58:40+00:00",
                OBSERVED,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    adapter = SqliteArxivObservationReplayAdapter(
        schema.connect,
        lambda requested: body if requested == object_id else b"",
    )
    with pytest.raises(
        ArxivObservationReplayError,
        match="arxiv_observation_capture_ambiguous",
    ):
        adapter(OBSERVATION)


def test_binding_query_mismatch_is_rejected_before_raw_read(tmp_path: Path) -> None:
    schema, _, body, _ = _setup(tmp_path)
    connection = schema.connect()
    try:
        connection.execute(
            "UPDATE source_bindings SET query_fingerprint=?",
            ("f" * 64,),
        )
        connection.commit()
    finally:
        connection.close()
    reads = []

    def read_object(object_id: str) -> bytes:
        reads.append(object_id)
        return body

    adapter = SqliteArxivObservationReplayAdapter(schema.connect, read_object)
    with pytest.raises(
        ArxivObservationReplayError,
        match="arxiv_observation_state_corrupt",
    ):
        adapter(OBSERVATION)
    assert reads == []


def test_disabled_binding_does_not_invalidate_saved_observation(
    tmp_path: Path,
) -> None:
    schema, _, body, object_id = _setup(tmp_path)
    connection = schema.connect()
    try:
        connection.execute(
            "UPDATE source_bindings SET enabled=0 "
            "WHERE id='binding:arxiv-replay'"
        )
        connection.commit()
    finally:
        connection.close()

    adapter = SqliteArxivObservationReplayAdapter(
        schema.connect,
        lambda requested: body if requested == object_id else b"",
    )

    replay = adapter(OBSERVATION)

    assert replay.observation_id == OBSERVATION
    assert replay.record.source_record_id == "2501.12345v1"
