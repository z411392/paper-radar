import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.sqlite_crossref_harvest_journal_adapter import (
    SqliteCrossrefHarvestJournalAdapter,
)
from libs.discovery.dtos.crossref_harvest import (
    CrossrefHarvestStepResult,
    CrossrefPendingItem,
)
from libs.discovery.exceptions.crossref_harvest_journal_error import (
    CrossrefHarvestJournalError,
)
from libs.research_workflow.application.commands.run_projected_crossref_harvest_window import (
    RunProjectedCrossrefHarvestWindow,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_store_adapter import (
    SqliteCrossrefIntegrityStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_integrity_work_binding_store_adapter import (
    SqliteCrossrefIntegrityWorkBindingStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_provider_revision_store_adapter import (
    SqliteCrossrefProviderRevisionStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_crossref_relation_store_adapter import (
    SqliteCrossrefRelationStoreAdapter,
)
from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.application.commands.bind_crossref_integrity_works import (
    BindCrossrefIntegrityWorks,
)
from libs.scholarly_catalog.application.commands.project_crossref_pending_item import (
    ProjectCrossrefPendingItem,
)
from libs.scholarly_catalog.application.commands.resolve_paper_identity import (
    ResolvePaperIdentity,
)
from libs.scholarly_catalog.application.queries.read_paper_identity import ReadPaperIdentity
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.paper_identity_observation import (
    PaperIdentityObservation,
)


NOW = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)
PAGE = "crossref-page:projection-replay"
PASS = "crossref-pass:projection-replay"


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _canonical(value: dict) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _setup(tmp_path: Path):
    path = tmp_path / "projection-replay.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0001-object-registry.sql",
        "0003-scholarly-catalog.sql",
        "0011-crossref-harvest.sql",
        "0018-crossref-provider-revisions.sql",
        "0019-crossref-projection-quarantines.sql",
        "0020-crossref-relation-assertions.sql",
        "0021-crossref-integrity-assertions.sql",
        "0022-crossref-integrity-work-bindings.sql",
    ):
        connection.executescript(
            (root / "migrations" / name).read_text(encoding="utf-8")
        )

    value = {
        "DOI": "10.1000/NOTICE",
        "relation": {
            "is-version-of": [
                {
                    "id-type": "doi",
                    "id": "10.1000/TARGET",
                    "asserted-by": "subject",
                }
            ]
        },
        "update-to": [
            {
                "DOI": "10.1000/TARGET",
                "type": "retraction",
                "source": "publisher",
                "label": "Retraction",
            }
        ],
    }
    canonical = _canonical(value)
    digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    connection.execute(
        "INSERT INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,'running',?,?)",
        (
            "crossref-window:projection-replay",
            "binding:projection-replay",
            "a" * 64,
            "v1",
            "2026-09-25T00:00:00+00:00",
            "2026-09-26T00:00:00+00:00",
            1000,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO crossref_harvest_passes("
        "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,"
        "started_at"
        ") VALUES(?,?,1,?,'running','*',?)",
        (PASS, "crossref-window:projection-replay", "b" * 64, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO crossref_harvest_pages("
        "id,pass_id,page_no,cursor_in,request_fingerprint,state,item_count,"
        "created_at,decoded_at"
        ") VALUES(?,?,0,'*',?,'decoded',1,?,?)",
        (PAGE, PASS, "c" * 64, NOW.isoformat(), NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO crossref_harvest_items("
        "page_id,ordinal,raw_doi,canonical_json,canonical_sha256,"
        "decode_state,outcome_state,created_at"
        ") VALUES(?,0,?,?,?,'decoded','pending',?)",
        (PAGE, "10.1000/NOTICE", canonical, digest, NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    connect = _connect(path)
    normalize = NormalizePaperIdentifier()
    identity_store = SqlitePaperIdentityStoreAdapter(connect)
    resolve_identity = ResolvePaperIdentity(normalize, identity_store)
    for doi, key, kind in (
        ("10.1000/NOTICE", "notice", "notice"),
        ("10.1000/TARGET", "target", "publication"),
    ):
        resolve_identity(
            PaperIdentityObservation(
                "obs:" + key,
                "doi",
                doi,
                "Paper " + key,
                hashlib.sha256(("content:" + key).encode()).hexdigest(),
                kind,
                "https://doi.org/" + doi.lower(),
                "published",
                NOW,
            )
        )

    bindings = BindCrossrefIntegrityWorks(
        ReadPaperIdentity(normalize, identity_store),
        SqliteCrossrefIntegrityWorkBindingStoreAdapter(connect),
    )
    project = ProjectCrossrefPendingItem(
        normalize,
        SqliteCrossrefProviderRevisionStoreAdapter(connect),
        SqliteCrossrefRelationStoreAdapter(connect),
        SqliteCrossrefIntegrityStoreAdapter(connect),
        bindings,
    )
    return path, connect, project


class Harvest:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self, plan, *, owner_id, max_pages, lease_seconds):
        del plan, owner_id, lease_seconds
        assert max_pages == 1
        return self.values.pop(0)


class RecordingProject:
    def __init__(self, delegate):
        self.delegate = delegate
        self.results = []

    def __call__(self, pending, *, observed_at):
        result = self.delegate(pending, observed_at=observed_at)
        self.results.append(result)
        return result


def _step(state: str, *, pending: int) -> CrossrefHarvestStepResult:
    return CrossrefHarvestStepResult(
        state,
        "crossref-window:projection-replay",
        PASS,
        PAGE,
        None,
        pending,
        "crossref_page_budget" if state == "page_committed" else None,
    )


def _counts(path: Path) -> tuple[int, int, int, int]:
    connection = sqlite3.connect(path)
    try:
        return (
            connection.execute(
                "SELECT count(*) FROM crossref_provider_revisions"
            ).fetchone()[0],
            connection.execute(
                "SELECT count(*) FROM crossref_relation_assertions"
            ).fetchone()[0],
            connection.execute(
                "SELECT count(*) FROM crossref_integrity_assertions"
            ).fetchone()[0],
            connection.execute(
                "SELECT count(*) FROM crossref_integrity_work_bindings"
            ).fetchone()[0],
        )
    finally:
        connection.close()


def test_catalog_projection_survives_journal_mark_rollback_and_replays_exactly(
    tmp_path: Path,
) -> None:
    path, connect, projector = _setup(tmp_path)
    journal = SqliteCrossrefHarvestJournalAdapter(connect)
    recording = RecordingProject(projector)
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TRIGGER inject_mark_failure "
        "BEFORE UPDATE OF outcome_state ON crossref_harvest_items "
        "WHEN OLD.outcome_state='pending' AND NEW.outcome_state='processed' "
        "BEGIN SELECT RAISE(ABORT,'injected_mark_failure'); END"
    )
    connection.commit()
    connection.close()

    first = RunProjectedCrossrefHarvestWindow(
        Harvest([_step("projection_required", pending=1)]),
        journal,
        recording,
        clock=lambda: NOW,
    )

    with pytest.raises(CrossrefHarvestJournalError):
        first(object(), owner_id="worker:test", max_pages=1, lease_seconds=60)

    assert recording.results[0].replayed is False
    assert _counts(path) == (1, 1, 1, 2)
    pending = journal.pending_items(PAGE)
    assert len(pending) == 1
    assert pending[0].ordinal == 0

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER inject_mark_failure")
    connection.commit()
    connection.close()

    second = RunProjectedCrossrefHarvestWindow(
        Harvest(
            [
                _step("projection_required", pending=1),
                _step("page_committed", pending=0),
            ]
        ),
        journal,
        recording,
        clock=lambda: NOW,
    )
    outcome = second(
        object(),
        owner_id="worker:test",
        max_pages=1,
        lease_seconds=60,
    )

    assert outcome.state == "page_committed"
    assert recording.results[1].replayed is True
    assert _counts(path) == (1, 1, 1, 2)
    assert journal.pending_items(PAGE) == ()

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        item = connection.execute(
            "SELECT outcome_state,canonical_doi,outcome_ref "
            "FROM crossref_harvest_items WHERE page_id=? AND ordinal=0",
            (PAGE,),
        ).fetchone()
    finally:
        connection.close()
    assert tuple(item) == (
        "processed",
        "10.1000/notice",
        recording.results[0].provider_revision_id,
    )
