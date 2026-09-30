import argparse
import json
import sys
from dataclasses import asdict

from injector import Injector

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_worker_env_file import read_worker_env_file
from apps.cli.module import WorkspaceEffectsCliModule
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.set_workspace_external_effects_port import (
    SetWorkspaceExternalEffectsPort,
)


def run_workspace_effects_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="paper-radar effects",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("enable", "disable"):
        command = commands.add_parser(operation, allow_abbrev=False)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--workspace")
        source.add_argument(
            "--env-file",
            help=(
                "Owner-only worker .env file; effects uses only "
                "PAPER_RADAR_WORKSPACE from it."
            ),
        )
    arguments = parser.parse_args(argv[1:])

    workspace = arguments.workspace
    if arguments.env_file is not None:
        try:
            values = read_worker_env_file(arguments.env_file)
            workspace = values.get("PAPER_RADAR_WORKSPACE")
        except ConfigurationFileError as exc:
            print(
                json.dumps(
                    {"error": {"code": exc.code}},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        except OSError:
            print(
                json.dumps(
                    {"error": {"code": "env_configuration_io_error"}},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from None

    if (
        not isinstance(workspace, str)
        or not workspace.strip()
        or "\0" in workspace
    ):
        parser.error("workspace must be a non-empty path")
    try:
        injector = Injector(
            [WorkspaceEffectsCliModule(workspace)],
            auto_bind=False,
        )
        result = injector.get(SetWorkspaceExternalEffectsPort)(
            arguments.operation == "enable"
        )
    except StorageError as exc:
        error: dict[str, str] = {"code": exc.code}
        if exc.code == "schema_upgrade_required":
            error["hint"] = (
                "Run init --workspace PATH --with-runtime explicitly before "
                "changing external effects."
            )
        elif exc.code == "workspace_missing":
            error["hint"] = (
                "Initialize the intended workspace explicitly; effects never "
                "creates one."
            )
        print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
