from pathlib import Path

import pytest

from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.exceptions.storage_error import StorageError


@pytest.mark.parametrize(
    "relative",
    [
        "../secret",
        "objects/../../secret",
        "/etc/passwd",
        "objects\\..\\secret",
    ],
)
def test_untrusted_relative_path_cannot_escape_workspace(
    tmp_path: Path,
    relative: str,
) -> None:
    managed = WorkspacePaths(tmp_path / "workspace")

    with pytest.raises(StorageError, match="unsafe_path"):
        managed.path(relative)


def test_untrusted_symlink_component_cannot_escape_workspace(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "objects").symlink_to(outside, target_is_directory=True)
    managed = WorkspacePaths(root)

    with pytest.raises(StorageError, match="unsafe_path"):
        managed.path("objects/secret")
