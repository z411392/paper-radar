import json
import re
from importlib.resources import files

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError


def bundled_domain_seeds_json() -> str:
    try:
        return (
            files("apps.cli")
            .joinpath("resources", "domain-seeds.json")
            .read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError) as exc:
        raise ConfigurationFileError("bundled_domain_seeds_unavailable") from exc


def mvp_profile_json(domains_csv: str, scope_text: str) -> str:
    if (
        not isinstance(domains_csv, str)
        or not domains_csv
        or len(domains_csv) > 1024
        or not isinstance(scope_text, str)
        or not scope_text.strip()
        or scope_text != scope_text.strip()
        or len(scope_text.encode("utf-8")) > 4096
        or "\0" in scope_text
    ):
        raise ConfigurationFileError("invalid_profile_env")

    domains = domains_csv.split(",")
    if (
        not 1 <= len(domains) <= 16
        or len(domains) != len(set(domains))
        or any(
            re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None
            for value in domains
        )
    ):
        raise ConfigurationFileError("invalid_profile_env")

    payload = {
        "id": "personal",
        "reader_id": "local",
        "name": "我的 arXiv 論文雷達",
        "scope_text": scope_text,
        "domains": [
            {"id": domain_id, "revision": 1}
            for domain_id in domains
        ],
        "filters": {
            "include": [],
            "exclude": [],
            "languages": ["en"],
            "sources": ["arxiv"],
            "free_only": True,
            "allow_preprints": True,
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
