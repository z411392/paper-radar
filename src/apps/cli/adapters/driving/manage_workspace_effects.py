import argparse
import json
import sys
from dataclasses import asdict

from injector import Injector

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
        command.add_argument("--workspace", required=True)
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\0" in arguments.workspace:
        parser.error("workspace must be a non-empty path")
    try:
        injector = Injector(
            [WorkspaceEffectsCliModule(arguments.workspace)],
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
