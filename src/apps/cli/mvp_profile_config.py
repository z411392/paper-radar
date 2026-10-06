import json
import re
from importlib.resources import files
from pathlib import Path

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError


MVP_DOMAIN_SOURCES = {
    "deep_learning": ("arxiv",),
    "machine_learning": ("arxiv",),
    "statistics": ("arxiv",),
    "badminton": ("pubmed",),
    "male_sexual_function": ("pubmed",),
}
SOURCE_ORDER = ("arxiv", "pubmed")


def bundled_domain_seeds_json() -> str:
    try:
        resource = files("apps.cli").joinpath(
            "resources",
            "domain-seeds.json",
        )
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
        source = (
            Path(__file__).resolve().parents[3]
            / "config"
            / "domain-seeds.json"
        )
        return source.read_text(encoding="utf-8")
    except (OSError, UnicodeError, IndexError) as exc:
        raise ConfigurationFileError(
            "bundled_domain_seeds_unavailable"
        ) from exc


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
            or value not in MVP_DOMAIN_SOURCES
            for value in domains
        )
    ):
        raise ConfigurationFileError("invalid_profile_env")

    selected_sources = {
        source
        for domain_id in domains
        for source in MVP_DOMAIN_SOURCES[domain_id]
    }
    sources = [
        source
        for source in SOURCE_ORDER
        if source in selected_sources
    ]

    languages = (
        ["english"]
        if sources == ["pubmed"]
        else ["en"]
        if sources == ["arxiv"]
        else []
    )
    payload = {
        "id": "personal",
        "reader_id": "local",
        "name": "我的論文雷達",
        "scope_text": scope_text,
        "domains": [
            {"id": domain_id, "revision": 1}
            for domain_id in domains
        ],
        "filters": {
            "include": [],
            "exclude": [],
            "languages": languages,
            "sources": sources,
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
