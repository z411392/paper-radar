import json

import pytest

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.mvp_profile_config import (
    bundled_domain_seeds_json,
    mvp_profile_json,
)


def test_mvp_profile_builder_keeps_arxiv_domains_on_arxiv() -> None:
    payload = json.loads(
        mvp_profile_json(
            "machine_learning,statistics",
            "關注機器學習與統計方法的新論文。",
        )
    )

    assert payload["id"] == "personal"
    assert payload["reader_id"] == "local"
    assert payload["filters"]["sources"] == ["arxiv"]
    assert payload["domains"] == [
        {"id": "machine_learning", "revision": 1},
        {"id": "statistics", "revision": 1},
    ]


@pytest.mark.parametrize(
    "domain_id",
    ["badminton", "male_reproductive_urology"],
)
def test_mvp_profile_builder_uses_pubmed_for_medical_domains(domain_id: str) -> None:
    payload = json.loads(
        mvp_profile_json(
            domain_id,
            "關注新的醫學研究。",
        )
    )

    assert payload["filters"]["sources"] == ["pubmed"]
    assert payload["domains"] == [
        {"id": domain_id, "revision": 1},
    ]


def test_mvp_profile_builder_can_represent_mixed_source_scope() -> None:
    payload = json.loads(
        mvp_profile_json(
            "deep_learning,badminton",
            "關注深度學習與羽球研究。",
        )
    )

    assert payload["filters"]["sources"] == ["arxiv", "pubmed"]


def test_mvp_profile_rejects_unrequested_legacy_domain() -> None:
    with pytest.raises(ConfigurationFileError) as exc:
        mvp_profile_json(
            "software_engineering",
            "關注軟體工程研究。",
        )

    assert exc.value.code == "invalid_profile_env"


def test_bundled_domain_seeds_contains_requested_domains() -> None:
    payload = json.loads(bundled_domain_seeds_json())
    ids = {item["id"] for item in payload["domains"]}

    assert {
        "deep_learning",
        "machine_learning",
        "statistics",
        "badminton",
        "male_reproductive_urology",
    } <= ids
