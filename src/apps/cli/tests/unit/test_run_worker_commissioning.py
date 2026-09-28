import pytest

from apps.cli.adapters.driving.run_worker import _parser, run_worker_cli


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
