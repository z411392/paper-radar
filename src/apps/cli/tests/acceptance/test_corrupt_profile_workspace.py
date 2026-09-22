import json
import subprocess
import sys
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.exceptions.storage_error import StorageError


def corrupt_workspace(tmp_path: Path) -> tuple[Path, Path, bytes]:
    root = tmp_path / "workspace"
    (root / "state").mkdir(parents=True)
    database = root / "state" / "app.sqlite3"
    content = b"not a database; private fixture content"
    database.write_bytes(content)
    return root, database, content


def test_schema_factory_normalizes_database_open_failure(tmp_path: Path) -> None:
    root, database, content = corrupt_workspace(tmp_path)
    factory = SqliteSchemaConnectionFactory(root, load_workspace_migrations(with_profiles=True))
    with pytest.raises(StorageError, match="schema_verification_failed"):
        factory.connect()
    assert database.read_bytes() == content


def test_profile_cli_does_not_leak_corrupt_database_error(tmp_path: Path) -> None:
    root, database, content = corrupt_workspace(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", "profile", "show", "--workspace", str(root),
         "--id", "personal"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "schema_verification_failed"
    assert "Traceback" not in result.stderr
    assert "private fixture content" not in result.stderr
    assert str(root) not in result.stderr
    assert database.read_bytes() == content
