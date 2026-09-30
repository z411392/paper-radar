import os
import stat

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError


def read_secret_file(path: str) -> str:
    """Read one owner-only bounded UTF-8 secret without following links."""
    if not isinstance(path, str) or not path or "\0" in path:
        raise ConfigurationFileError("invalid_secret_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationFileError("secret_not_regular")
        if metadata.st_uid != os.getuid():
            raise ConfigurationFileError("secret_wrong_owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ConfigurationFileError("secret_permissions_too_open")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(8193)
        if len(content) > 8192:
            raise ConfigurationFileError("secret_too_large")
        try:
            value = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigurationFileError("invalid_secret_utf8") from exc
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        if (
            not value
            or "\0" in value
            or "\n" in value
            or "\r" in value
            or any(ord(char) == 127 for char in value)
        ):
            raise ConfigurationFileError("invalid_secret_value")
        return value
    finally:
        os.close(fd)
