import json
from dataclasses import dataclass

import pytest

import apps.cli.adapters.driving.manage_reading_history as cli
from libs.delivery.dtos.local_reading_history import LocalReadingHistory
from libs.delivery.exceptions.local_reading_history_error import (
    LocalReadingHistoryError,
)
from libs.scholarly_catalog.dtos.local_paper_record import LocalPaperRecord


@dataclass
class FakePort:
    value: object

    def __call__(self, work_id, reader_id, channel=None):
        assert work_id == "work:test"
        assert reader_id == "reader:test"
        assert channel == "email"
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeInjector:
    port = None

    def __init__(self, modules, auto_bind):
        assert len(modules) == 1
        assert auto_bind is False

    def get(self, port_type):
        del port_type
        assert self.port is not None
        return self.port


def _history():
    return LocalReadingHistory(
        LocalPaperRecord(
            "work:test",
            "work:test",
            "Paper",
            "published",
            "2026",
            "year",
            ("work:test",),
            (),
        ),
        (),
    )


def test_cli_emits_json_without_mutating_data(monkeypatch, capsys) -> None:
    FakeInjector.port = FakePort(_history())
    monkeypatch.setattr(cli, "Injector", FakeInjector)

    cli.run_reading_history_cli(
        [
            "reading",
            "show",
            "--workspace",
            "/tmp/workspace",
            "--work-id",
            "work:test",
            "--reader-id",
            "reader:test",
            "--channel",
            "email",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["paper"]["canonical_work_id"] == "work:test"
    assert payload["notifications"] == []


def test_cli_preserves_owner_error_instead_of_printing_empty(
    monkeypatch,
    capsys,
) -> None:
    FakeInjector.port = FakePort(
        LocalReadingHistoryError("local_reading_history_corrupt")
    )
    monkeypatch.setattr(cli, "Injector", FakeInjector)

    with pytest.raises(SystemExit) as raised:
        cli.run_reading_history_cli(
            [
                "reading",
                "show",
                "--workspace",
                "/tmp/workspace",
                "--work-id",
                "work:test",
                "--reader-id",
                "reader:test",
                "--channel",
                "email",
            ]
        )

    assert raised.value.code == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": {"code": "local_reading_history_corrupt"}
    }
