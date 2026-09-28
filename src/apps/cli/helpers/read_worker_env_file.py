import os
import re
import stat

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError


WORKER_ENV_KEYS = frozenset(
    {
        "PAPER_RADAR_WORKSPACE",
        "PAPER_RADAR_ALLOW_LIVE_SOURCE",
        "PAPER_RADAR_ALLOW_LIVE_MODEL",
        "PAPER_RADAR_ALLOW_LIVE_MAIL",
        "PAPER_RADAR_OPENROUTER_API_KEY",
        "PAPER_RADAR_MODEL_MONTHLY_BUDGET_USD",
        "PAPER_RADAR_MODEL_RESERVATION_USD",
        "PAPER_RADAR_RECIPIENT_EMAIL",
        "PAPER_RADAR_SMTP_HOST",
        "PAPER_RADAR_SMTP_PORT",
        "PAPER_RADAR_SMTP_SENDER",
        "PAPER_RADAR_SMTP_USERNAME",
        "PAPER_RADAR_SMTP_PASSWORD",
        "PAPER_RADAR_SMTP_SECURITY",
        "PAPER_RADAR_DIGEST_TIMEZONE",
        "PAPER_RADAR_DIGEST_LOCAL_TIME",
        "PAPER_RADAR_DIGEST_MAX_ITEMS",
        "PAPER_RADAR_PROFILE_DOMAINS",
        "PAPER_RADAR_PROFILE_SCOPE",
        "PAPER_RADAR_POLL_SECONDS",
        "PAPER_RADAR_MAX_NEW_JOBS",
        "PAPER_RADAR_MAX_JOBS",
        "PAPER_RADAR_LEASE_SECONDS",
    }
)


def read_worker_env_file(path: str) -> dict[str, str]:
    """Read one owner-only, non-executable worker .env file."""

    if not isinstance(path, str) or not path or "\0" in path:
        raise ConfigurationFileError("invalid_env_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationFileError("env_not_regular")
        if metadata.st_uid != os.getuid():
            raise ConfigurationFileError("env_wrong_owner")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ConfigurationFileError("env_permissions_too_open")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise ConfigurationFileError("env_too_large")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigurationFileError("invalid_env_utf8") from exc
        if "\0" in text:
            raise ConfigurationFileError("invalid_env_value")

        values: dict[str, str] = {}
        for raw in text.splitlines():
            if not raw or raw.startswith("#"):
                continue
            if raw != raw.strip() or "=" not in raw:
                raise ConfigurationFileError("invalid_env_line")
            key, value = raw.split("=", 1)
            if (
                re.fullmatch(r"PAPER_RADAR_[A-Z0-9_]+", key) is None
                or key not in WORKER_ENV_KEYS
            ):
                raise ConfigurationFileError("unknown_env_key")
            if key in values:
                raise ConfigurationFileError("duplicate_env_key")
            if (
                not value
                or value != value.strip()
                or "\r" in value
                or "\n" in value
                or "\0" in value
            ):
                raise ConfigurationFileError("invalid_env_value")
            values[key] = value
        if not values:
            raise ConfigurationFileError("empty_env_file")
        return values
    finally:
        os.close(fd)
