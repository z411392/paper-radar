import json

import pytest

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.mvp_profile_config import (
    bundled_domain_seeds_json,
    mvp_profile_json,
)


def test_mvp_profile_builder_uses_arxiv_only_sources() -> None:
    payload = json.loads(
        mvp_profile_json(
            "machine_learning,statistics",
            "關注機器學習與統計方法的新 arXiv 論文。",
        )
    )

    assert payload["id"] == "personal"
    assert payload["reader_id"] == "local"
    assert payload["filters"]["sources"] == ["arxiv"]
    assert payload["domains"] == [
        {"id": "machine_learning", "revision": 1},
        {"id": "statistics", "revision": 1},
    ]


def test_mvp_profile_rejects_domain_without_arxiv_coverage() -> None:
    with pytest.raises(ConfigurationFileError) as exc:
        mvp_profile_json(
            "badminton",
            "關注羽球研究。",
        )

    assert exc.value.code == "invalid_profile_env"


def test_bundled_domain_seeds_contains_mvp_domains() -> None:
    payload = json.loads(bundled_domain_seeds_json())
    ids = {item["id"] for item in payload["domains"]}

    assert {
        "software_engineering",
        "deep_learning",
        "machine_learning",
        "statistics",
    } <= ids
