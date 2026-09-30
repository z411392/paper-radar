from pathlib import Path

import pytest

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_secret_file import read_secret_file


def test_reads_owner_only_secret_and_removes_one_terminal_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "smtp-password"
    path.write_bytes("秘密 value\n".encode("utf-8"))
    path.chmod(0o600)

    assert read_secret_file(str(path)) == "秘密 value"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o666])
def test_secret_file_rejects_group_or_other_permissions(
    tmp_path: Path,
    mode: int,
) -> None:
    path = tmp_path / "smtp-password"
    path.write_text("secret", encoding="utf-8")
    path.chmod(mode)

    with pytest.raises(
        ConfigurationFileError,
        match="secret_permissions_too_open",
    ):
        read_secret_file(str(path))


def test_secret_symlink_is_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "actual"
    target.write_text("secret", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(OSError):
        read_secret_file(str(link))


def test_secret_file_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "smtp-password"
    path.write_bytes(b"x" * 8193)
    path.chmod(0o600)

    with pytest.raises(ConfigurationFileError, match="secret_too_large"):
        read_secret_file(str(path))


@pytest.mark.parametrize("payload", [b"", b"a\nb", b"a\x00b", b"bad\xff"])
def test_invalid_secret_content_is_rejected_without_echo(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = tmp_path / "smtp-password"
    path.write_bytes(payload)
    path.chmod(0o600)

    with pytest.raises(ConfigurationFileError):
        read_secret_file(str(path))
