import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_provider_revision_store_adapter import (
    SqliteCrossrefProviderRevisionStoreAdapter,
)
from libs.scholarly_catalog.application.commands.project_crossref_pending_item import (
    ProjectCrossrefPendingItem,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


NOW = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "crossref-provider.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        (root / "migrations/0017-crossref-provider-revisions.sql").read_text(
            encoding="utf-8"
        )
    )
    connection.commit()
    connection.close()
    store = SqliteCrossrefProviderRevisionStoreAdapter(_connect(path))
    return path, ProjectCrossrefPendingItem(NormalizePaperIdentifier(), store)


def _canonical(value: dict) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _item(
    value: dict,
    *,
    page_id: str = "crossref-page:one",
    ordinal: int = 0,
) -> CrossrefPendingItem:
    canonical = _canonical(value)
    return CrossrefPendingItem(
        page_id,
        ordinal,
        value.get("DOI"),
        canonical,
        hashlib.sha256(canonical.encode("ascii")).hexdigest(),
    )


def test_missing_title_is_still_a_durable_provider_revision(tmp_path: Path) -> None:
    _, project = _setup(tmp_path)
    item = _item(
        {
            "DOI": "10.1000/ABC",
            "indexed": {"date-time": "2026-09-25T05:00:00Z"},
            "created": {"date-time": "2026-09-20T01:02:03Z"},
            "deposited": {"date-time": "2026-09-24T04:05:06Z"},
            "published": {"date-parts": [[2026, 9]]},
        }
    )

    result = project(item, observed_at=NOW)

    assert result.canonical_doi == "10.1000/abc"
    assert result.provider_revision_id.startswith("crossref-provider-revision:")
    assert result.replayed is False
    assert result.title is None
    assert result.published_date == "2026-09"
    assert result.published_precision == "month"


def test_exact_provider_item_replay_reuses_revision_and_adds_observation(
    tmp_path: Path,
) -> None:
    path, project = _setup(tmp_path)
    value = {"DOI": "10.1000/ABC", "title": ["Paper"]}
    first = project(_item(value), observed_at=NOW)
    second = project(
        _item(value, page_id="crossref-page:two", ordinal=3),
        observed_at=NOW,
    )

    assert second.provider_revision_id == first.provider_revision_id
    assert second.replayed is True
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM crossref_provider_revisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM crossref_provider_observations"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_provider_only_counter_change_adds_revision_without_semantic_change(
    tmp_path: Path,
) -> None:
    _, project = _setup(tmp_path)
    base = {
        "DOI": "10.1000/ABC",
        "title": ["Paper"],
        "author": [{"family": "Lin", "given": "Wei"}],
        "is-referenced-by-count": 1,
        "reference-count": 3,
        "indexed": {"date-time": "2026-09-24T00:00:00Z"},
    }
    changed = dict(base)
    changed["is-referenced-by-count"] = 2
    changed["reference-count"] = 4
    changed["indexed"] = {"date-time": "2026-09-25T00:00:00Z"}

    first = project(_item(base), observed_at=NOW)
    second = project(
        _item(changed, page_id="crossref-page:two"),
        observed_at=NOW,
    )

    assert second.provider_revision_id != first.provider_revision_id
    assert second.semantic_sha256 == first.semantic_sha256
    assert second.replayed is False


def test_same_page_ordinal_cannot_be_rebound_to_different_provider_item(
    tmp_path: Path,
) -> None:
    _, project = _setup(tmp_path)
    project(_item({"DOI": "10.1000/A"}), observed_at=NOW)

    with pytest.raises(
        CrossrefProviderProjectionError,
        match="crossref_provider_observation_conflict",
    ):
        project(_item({"DOI": "10.1000/B"}), observed_at=NOW)


def test_canonical_json_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    _, project = _setup(tmp_path)
    item = _item({"DOI": "10.1000/A"})
    corrupt = CrossrefPendingItem(
        item.page_id,
        item.ordinal,
        item.raw_doi,
        item.canonical_json,
        "f" * 64,
    )

    with pytest.raises(
        CrossrefProviderProjectionError,
        match="crossref_provider_item_mismatch",
    ):
        project(corrupt, observed_at=NOW)


def test_two_writers_are_idempotent_for_same_provider_revision(
    tmp_path: Path,
) -> None:
    path, _ = _setup(tmp_path)
    connect = _connect(path)
    item = _item({"DOI": "10.1000/A", "title": ["Paper"]})

    def run(page: str):
        projector = ProjectCrossrefPendingItem(
            NormalizePaperIdentifier(),
            SqliteCrossrefProviderRevisionStoreAdapter(connect),
        )
        return projector(
            CrossrefPendingItem(
                page,
                0,
                item.raw_doi,
                item.canonical_json,
                item.canonical_sha256,
            ),
            observed_at=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("crossref-page:a", "crossref-page:b")))

    assert len({result.provider_revision_id for result in results}) == 1
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM crossref_provider_revisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM crossref_provider_observations"
        ).fetchone()[0] == 2
    finally:
        connection.close()
