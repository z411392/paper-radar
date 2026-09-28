from datetime import datetime, timezone

import pytest

from apps.cli.adapters.driving.run_worker import _parser, run_worker_cli
from apps.cli.model_commissioning import (
    commissioned_openrouter_budget_period,
    model_budget_usd_to_micros,
)


def parse(*args: str):
    return _parser().parse_args(["--workspace", "/tmp/workspace", *args])


def test_live_source_parser_leaves_provider_overrides_optional() -> None:
    value = parse("--allow-live-source")
    assert value.rate_limit_state is None
    assert value.ncbi_email is None
    assert value.ncbi_rate_limit_state is None
    assert value.crossref_email is None
    assert value.crossref_rate_limit_dir is None
    assert value.allow_live_mail is False
    assert value.recipient_email is None
    assert value.recipient_map_file is None
    assert value.smtp_host is None
    assert value.smtp_port is None
    assert value.smtp_sender is None
    assert value.smtp_username is None
    assert value.smtp_password_file is None


def test_ncbi_commissioning_arguments_are_independent_from_arxiv() -> None:
    value = parse(
        "--allow-live-source",
        "--ncbi-email",
        "reader@example.com",
        "--ncbi-rate-limit-state",
        "/tmp/ncbi-rate.json",
    )
    assert value.rate_limit_state is None
    assert value.ncbi_email == "reader@example.com"
    assert value.ncbi_rate_limit_state == "/tmp/ncbi-rate.json"


def test_parser_shape_keeps_api_key_optional() -> None:
    value = parse(
        "--allow-live-source",
        "--ncbi-email",
        "reader@example.com",
        "--ncbi-rate-limit-state",
        "/tmp/ncbi-rate.json",
    )
    assert value.ncbi_api_key is None


def test_crossref_commissioning_arguments_are_independent_from_other_providers() -> None:
    value = parse(
        "--allow-live-source",
        "--crossref-email",
        "reader@example.com",
        "--crossref-rate-limit-dir",
        "/tmp/crossref-rate",
    )

    assert value.rate_limit_state is None
    assert value.ncbi_email is None
    assert value.crossref_email == "reader@example.com"
    assert value.crossref_rate_limit_dir == "/tmp/crossref-rate"


def test_mail_commissioning_is_independent_from_live_sources() -> None:
    value = parse(
        "--allow-live-mail",
        "--recipient-map-file",
        "/tmp/recipients.json",
        "--smtp-host",
        "smtp.example.com",
        "--smtp-port",
        "465",
        "--smtp-sender",
        "paper-radar@example.com",
        "--smtp-username",
        "mailer@example.com",
        "--smtp-password-file",
        "/tmp/smtp-password",
    )

    assert value.allow_live_source is False
    assert value.allow_live_mail is True
    assert value.smtp_port == 465
    assert value.ncbi_email is None
    assert value.crossref_email is None


def test_partial_mail_options_require_explicit_mail_switch(capsys) -> None:
    with pytest.raises(SystemExit):
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--smtp-host",
                "smtp.example.com",
            ]
        )
    assert "mail options require --allow-live-mail" in capsys.readouterr().err


def test_live_mail_requires_complete_configuration(capsys) -> None:
    with pytest.raises(SystemExit):
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--allow-live-mail",
            ]
        )
    assert "--allow-live-mail requires" in capsys.readouterr().err


def test_live_mail_rejects_relative_local_file_paths(capsys) -> None:
    with pytest.raises(SystemExit):
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--allow-live-mail",
                "--recipient-map-file",
                "recipients.json",
                "--smtp-host",
                "smtp.example.com",
                "--smtp-port",
                "465",
                "--smtp-sender",
                "paper-radar@example.com",
                "--smtp-username",
                "mailer@example.com",
                "--smtp-password-file",
                "password",
            ]
        )
    assert "--recipient-map-file must be an absolute path" in capsys.readouterr().err


def test_model_policy_fingerprint_is_not_an_operator_argument() -> None:
    with pytest.raises(SystemExit):
        parse("--model-policy-fingerprint", "a" * 64)


@pytest.mark.parametrize(
    "option,value",
    [
        ("--model-period-key", "2026-09"),
        ("--model-currency", "USD"),
    ],
)
def test_model_accounting_identity_is_not_an_operator_argument(
    option: str,
    value: str,
) -> None:
    with pytest.raises(SystemExit):
        parse(option, value)


def test_openrouter_budget_period_uses_utc_month_and_usd() -> None:
    assert commissioned_openrouter_budget_period(
        datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc)
    ) == ("2026-09", "USD")


def test_single_recipient_mail_does_not_require_map_file() -> None:
    value = parse(
        "--allow-live-mail",
        "--recipient-email",
        "reader@example.com",
        "--smtp-host",
        "smtp.example.com",
        "--smtp-port",
        "465",
        "--smtp-sender",
        "paper-radar@example.com",
        "--smtp-username",
        "mailer@example.com",
        "--smtp-password-file",
        "/tmp/smtp-password",
    )

    assert value.recipient_email == "reader@example.com"
    assert value.recipient_map_file is None


def test_live_mail_rejects_direct_email_and_map_file_together(
    capsys,
) -> None:
    with pytest.raises(SystemExit):
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--allow-live-mail",
                "--recipient-email",
                "reader@example.com",
                "--recipient-map-file",
                "/tmp/recipients.json",
                "--smtp-host",
                "smtp.example.com",
                "--smtp-port",
                "465",
                "--smtp-sender",
                "paper-radar@example.com",
                "--smtp-username",
                "mailer@example.com",
                "--smtp-password-file",
                "/tmp/password",
            ]
        )
    assert "requires exactly one of --recipient-email" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value,micros",
    [
        ("1", 1_000_000),
        ("1.00", 1_000_000),
        ("0.01", 10_000),
        ("0.000001", 1),
    ],
)
def test_model_budget_usd_converts_exactly(
    value: str,
    micros: int,
) -> None:
    assert model_budget_usd_to_micros(value) == micros


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "1.0000001",
        "1e-3",
        " 1.00",
        "1.00 ",
    ],
)
def test_model_budget_usd_rejects_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid_model_budget_usd"):
        model_budget_usd_to_micros(value)
