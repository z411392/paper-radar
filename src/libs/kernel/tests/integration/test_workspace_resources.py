import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from libs.kernel.exceptions.storage_error import StorageError


def test_packaged_migration_matches_the_only_canonical_sql() -> None:
    from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations

    root = Path(__file__).resolve().parents[5]
    migrations = load_workspace_migrations()
    assert len(migrations) == 1
    assert migrations[0].version == 1
    assert migrations[0].name == "0001-object-registry.sql"
    assert migrations[0].sql.encode("utf-8") == (root / "migrations" / migrations[0].name).read_bytes()


def test_missing_resource_is_not_replaced_with_cwd_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import libs.kernel.adapters.driven.bundled_workspace_migrations as loader

    monkeypatch.setattr(loader, "files", lambda anchor: tmp_path)
    with pytest.raises(StorageError, match="migration_resource_error"):
        loader.load_workspace_migrations()
    assert list(tmp_path.iterdir()) == []


def test_sdist_preserves_the_canonical_migration(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[5]
    result = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "sdist", "-d", str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    archives = list(tmp_path.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as archive:
        matches = [n for n in archive.getnames() if n.endswith("/migrations/0001-object-registry.sql")]
        assert len(matches) == 1
        stream = archive.extractfile(matches[0])
        assert stream is not None
        assert stream.read() == (root / "migrations/0001-object-registry.sql").read_bytes()


def test_runtime_bundle_contains_exact_migrations_0001_through_0019() -> None:
    from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations

    root = Path(__file__).resolve().parents[5]
    selected = load_workspace_migrations(with_runtime=True)
    assert [migration.version for migration in selected] == list(range(1, 20))
    assert [migration.name for migration in selected] == [
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0004-discovery.sql",
        "0005-paper-explanations.sql",
        "0006-retrieval.sql",
        "0007-delivery.sql",
        "0008-workflow-jobs.sql",
        "0009-relevance-assessment-domains.sql",
        "0010-pubmed-harvest.sql",
        "0011-crossref-harvest.sql",
        "0012-crossref-repair.sql",
        "0013-crossref-window-splits.sql",
        "0014-crossref-capture-claims.sql",
        "0015-crossref-capture-inbox.sql",
        "0016-crossref-capture-resolutions.sql",
        "0017-crossref-provider-revisions.sql",
        "0018-crossref-projection-quarantines.sql",
        "0019-crossref-relation-assertions.sql",
    ]
    for migration in selected:
        assert migration.sql.encode("utf-8") == (root / "migrations" / migration.name).read_bytes()
