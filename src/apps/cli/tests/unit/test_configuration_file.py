import os
from pathlib import Path

import pytest

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_configuration_file import read_configuration_file


def test_reads_utf8_without_modifying_input(tmp_path: Path) -> None:
    path = tmp_path / "關注.json"
    original = '{"scope_text":"機器學習"}'.encode()
    path.write_bytes(original)
    assert read_configuration_file(str(path)) == original.decode()
    assert path.read_bytes() == original


@pytest.mark.parametrize("size", [1_000_000, 1_000_001])
def test_size_limit_is_enforced_on_read(tmp_path: Path, size: int) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b" " * size)
    if size == 1_000_000:
        assert len(read_configuration_file(str(path))) == size
    else:
        with pytest.raises(ConfigurationFileError, match="configuration_too_large"):
            read_configuration_file(str(path))


def test_invalid_utf8_does_not_leak_payload(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_bytes(b"private value: \xff")
    with pytest.raises(ConfigurationFileError, match="^invalid_utf8$"):
        read_configuration_file(str(path))


def test_missing_input_does_not_create_it(tmp_path: Path) -> None:
    path = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        read_configuration_file(str(path))
    assert not path.exists()


def test_fifo_is_rejected_without_waiting_for_a_writer(tmp_path: Path) -> None:
    path = tmp_path / "fifo"
    os.mkfifo(path)
    with pytest.raises(ConfigurationFileError, match="configuration_not_regular"):
        read_configuration_file(str(path))


def test_symlink_is_not_followed(tmp_path: Path) -> None:
    target = tmp_path / "actual.json"
    target.write_bytes(b"{}")
    link = tmp_path / "input.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        read_configuration_file(str(link))
    assert target.read_bytes() == b"{}"


@pytest.mark.parametrize("path", ["", "bad\x00path"])
def test_invalid_path_is_rejected(path: str) -> None:
    with pytest.raises(ConfigurationFileError, match="invalid_configuration_path"):
        read_configuration_file(path)
