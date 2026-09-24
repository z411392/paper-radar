import pytest

from apps.cli.adapters.driving.run_worker import run_worker_cli


def test_ncbi_options_require_global_live_source_flag(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--ncbi-email",
                "reader@example.com",
                "--ncbi-rate-limit-state",
                "/tmp/ncbi-rate",
            ]
        )

    assert error.value.code == 2
    assert "live-source options require --allow-live-source" in capsys.readouterr().err


def test_ncbi_email_and_rate_state_are_an_explicit_pair(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--allow-live-source",
                "--rate-limit-state",
                "/tmp/arxiv-rate",
                "--ncbi-email",
                "reader@example.com",
            ]
        )

    assert error.value.code == 2
    assert "must be supplied together" in capsys.readouterr().err


def test_ncbi_rate_state_must_be_absolute_before_injector_creation(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--allow-live-source",
                "--rate-limit-state",
                "/tmp/arxiv-rate",
                "--ncbi-email",
                "reader@example.com",
                "--ncbi-rate-limit-state",
                "relative/ncbi-rate",
            ]
        )

    assert error.value.code == 2
    assert "must be an absolute path" in capsys.readouterr().err


@pytest.mark.parametrize(
    "email",
    ["missing-at", "@example.com", "reader@", "reader @example.com"],
)
def test_ncbi_contact_email_is_validated_before_composition(email: str, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        run_worker_cli(
            [
                "run-worker",
                "--workspace",
                "/tmp/workspace",
                "--allow-live-source",
                "--rate-limit-state",
                "/tmp/arxiv-rate",
                "--ncbi-email",
                email,
                "--ncbi-rate-limit-state",
                "/tmp/ncbi-rate",
            ]
        )

    assert error.value.code == 2
    assert "valid contact email" in capsys.readouterr().err
