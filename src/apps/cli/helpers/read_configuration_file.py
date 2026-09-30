import os
import stat

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError


def read_configuration_file(path: str) -> str:
    """Read a bounded UTF-8 CLI payload, never a FIFO, device, or symlink."""
    if not path or "\x00" in path:
        raise ConfigurationFileError("invalid_configuration_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ConfigurationFileError("configuration_not_regular")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(1_000_001)
        if len(content) > 1_000_000:
            raise ConfigurationFileError("configuration_too_large")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigurationFileError("invalid_utf8") from exc
    finally:
        os.close(fd)
