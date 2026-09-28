from pathlib import Path

import pytest

from apps.cli.adapters.driving.run_worker import run_worker_cli
from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_worker_env_file import read_worker_env_file


def _write(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_reads_whitelisted_worker_env_without_shell_semantics(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "worker.env",
        "\n".join(
            (
                "# Paper Radar runtime",
                "PAPER_RADAR_WORKSPACE=/tmp/paper-radar",
                "PAPER_RADAR_ALLOW_LIVE_SOURCE=true",
                "PAPER_RADAR_OPENROUTER_API_KEY=literal_$HOME_not_expanded",
            )
        )
        + "\n",
    )

    assert read_worker_env_file(str(path)) == {
        "PAPER_RADAR_WORKSPACE": "/tmp/paper-radar",
        "PAPER_RADAR_ALLOW_LIVE_SOURCE": "true",
        "PAPER_RADAR_OPENROUTER_API_KEY": "literal_$HOME_not_expanded",
    }


@pytest.mark.parametrize(
    "content,code",
    [
        (
            "PAPER_RADAR_WORKSPACE=/tmp/a\n"
            "PAPER_RADAR_WORKSPACE=/tmp/b\n",
            "duplicate_env_key",
        ),
        ("UNKNOWN=value\n", "unknown_env_key"),
        (" export PAPER_RADAR_WORKSPACE=/tmp/a\n", "invalid_env_line"),
        ("PAPER_RADAR_WORKSPACE= /tmp/a\n", "invalid_env_value"),
    ],
)
def test_rejects_ambiguous_or_unknown_env_content(
    tmp_path: Path,
    content: str,
    code: str,
) -> None:
    path = _write(tmp_path / "worker.env", content)

    with pytest.raises(ConfigurationFileError) as exc:
        read_worker_env_file(str(path))

    assert exc.value.code == code


def test_rejects_group_readable_worker_env(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "worker.env",
        "PAPER_RADAR_WORKSPACE=/tmp/paper-radar\n",
        mode=0o640,
    )

    with pytest.raises(ConfigurationFileError) as exc:
        read_worker_env_file(str(path))

    assert exc.value.code == "env_permissions_too_open"


def test_env_file_mode_rejects_runtime_flag_mixing(
    tmp_path: Path,
    capsys,
) -> None:
    path = _write(
        tmp_path / "worker.env",
        "PAPER_RADAR_WORKSPACE=/tmp/paper-radar\n",
    )

    with pytest.raises(SystemExit):
        run_worker_cli(
            [
                "run-worker",
                "--env-file",
                str(path),
                "--workspace",
                "/tmp/other",
                "--once",
            ]
        )

    assert "cannot be combined with runtime configuration flags" in (
        capsys.readouterr().err
    )



def test_live_source_env_requires_profile_config(
    tmp_path: Path,
    capsys,
) -> None:
    path = _write(
        tmp_path / "worker.env",
        "\n".join(
            (
                "PAPER_RADAR_WORKSPACE=/tmp/paper-radar",
                "PAPER_RADAR_ALLOW_LIVE_SOURCE=true",
            )
        )
        + "\n",
    )

    with pytest.raises(SystemExit):
        run_worker_cli(
            [
                "run-worker",
                "--env-file",
                str(path),
                "--once",
            ]
        )

    assert "requires profile domains and scope" in capsys.readouterr().err
