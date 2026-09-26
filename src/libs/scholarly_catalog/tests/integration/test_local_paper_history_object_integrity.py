from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.scholarly_catalog.adapters.driven.sqlite_local_paper_history_adapter import (
    SqliteLocalPaperHistoryAdapter,
)
from libs.scholarly_catalog.application.queries.read_local_paper_record import (
    ReadLocalPaperRecord,
)
from libs.scholarly_catalog.exceptions.local_paper_history_error import (
    LocalPaperHistoryError,
)


NOW = "2026-09-26T00:00:00+00:00"


def _fixture(tmp_path: Path):
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()

    raw = SqliteConnectionFactory(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    files = FilesystemObjectBytesAdapter(root)
    published = PublishObject(files, objects)(
        b"stored abstract",
        "raw",
        "text/plain",
        "source-response",
    )

    connection = raw.connect()
    try:
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                "work:local-history",
                "Stored paper",
                "published",
                "2026-09-25",
                "day",
                NOW,
                NOW,
            ),
        )
        connection.execute(
            "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
            (
                "manifest:local-history",
                "work:local-history",
                "doi",
                "10.1000/local-history",
                "publication",
                "https://doi.org/10.1000/local-history",
                NOW,
            ),
        )
        connection.execute(
            "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "revision:local-history",
                "manifest:local-history",
                "work:local-history",
                None,
                "a" * 64,
                "Stored paper",
                published.object_id,
                NOW,
                "2026-09-25",
                "day",
                NOW,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    query = ReadLocalPaperRecord(
        SqliteLocalPaperHistoryAdapter(
            raw.connect,
            ReadObject(files, objects),
        )
    )
    return root, published, query


def test_physical_abstract_loss_is_not_reported_as_readable_history(
    tmp_path: Path,
) -> None:
    root, published, query = _fixture(tmp_path)

    readable = query("work:local-history")
    assert readable.manifestations[0].revisions[0].revision_id == (
        "revision:local-history"
    )

    (root / published.relative_path).unlink()

    with pytest.raises(
        LocalPaperHistoryError,
        match="local_paper_object_unavailable",
    ):
        query("work:local-history")


def test_physical_abstract_corruption_is_not_reported_as_readable_history(
    tmp_path: Path,
) -> None:
    root, published, query = _fixture(tmp_path)
    (root / published.relative_path).write_bytes(b"corrupt")

    with pytest.raises(
        LocalPaperHistoryError,
        match="local_paper_object_unavailable",
    ):
        query("work:local-history")
