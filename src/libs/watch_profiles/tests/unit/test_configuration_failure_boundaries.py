import json
import sys

import pytest

from libs.watch_profiles.application.commands.set_watch_profile_lifecycle import SetWatchProfileLifecycle
from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError


def test_integer_decoder_limit_becomes_a_configuration_error() -> None:
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        document = '{"domains":' + "9" * 641 + "}"
        normalize = NormalizeWatchConfiguration(frozenset({"arxiv"}))
        with pytest.raises(WatchConfigurationError) as caught:
            normalize("domains", document)
        assert caught.value.code == "invalid_json"
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize(
    ("document", "code"),
    [
        ('{"domains":[],"domains":[]}', "duplicate_key"),
        ('{"domains":NaN}', "invalid_number"),
        ('{"domains":Infinity}', "invalid_number"),
        ('{"domains":-Infinity}', "invalid_number"),
        ('{"domains":[]}', "invalid_domains"),
        ('{"domains":', "invalid_json"),
    ],
)
def test_specific_validation_errors_are_not_reclassified(document: str, code: str) -> None:
    normalize = NormalizeWatchConfiguration(frozenset({"arxiv"}))
    with pytest.raises(WatchConfigurationError) as caught:
        normalize("domains", document)
    assert caught.value.code == code


@pytest.mark.parametrize("invalid", [None, True, 0, [], {}, {"paused"}, "archived", ""])
def test_lifecycle_type_is_rejected_before_store_access(invalid: object) -> None:
    class NoWrites:
        def set_lifecycle(self, profile_id: str, lifecycle: str) -> None:
            pytest.fail("Invalid lifecycle reached persistence")

    command = SetWatchProfileLifecycle(NoWrites())
    with pytest.raises(WatchConfigurationError) as caught:
        command("my_reading", invalid)
    assert caught.value.code == "invalid_lifecycle"


@pytest.mark.parametrize("lifecycle", ["active", "paused"])
def test_valid_lifecycle_still_reaches_the_same_store_contract(lifecycle: str) -> None:
    recorded = []

    class Recorder:
        def set_lifecycle(self, profile_id: str, selected: str) -> None:
            recorded.append((profile_id, selected))

    SetWatchProfileLifecycle(Recorder())("my_reading", lifecycle)
    assert recorded == [("my_reading", lifecycle)]


def test_integer_decoder_setting_is_not_changed_by_normalizer() -> None:
    previous = sys.get_int_max_str_digits()
    normalize = NormalizeWatchConfiguration(frozenset({"arxiv"}))
    with pytest.raises(WatchConfigurationError):
        normalize("domains", json.dumps({"domains": 12}))
    assert sys.get_int_max_str_digits() == previous
