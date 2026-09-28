import argparse
import json
import sys
from functools import partial

from fire import Fire
from injector import Injector

from apps.cli.adapters.driving.initialize_workspace import initialize_workspace
from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_worker_env_file import read_worker_env_file
from apps.cli.adapters.driving.manage_digest import run_digest_cli
from apps.cli.adapters.driving.manage_harvest import run_harvest_cli
from apps.cli.adapters.driving.manage_watch_profiles import run_watch_cli
from apps.cli.adapters.driving.manage_workspace_effects import (
    run_workspace_effects_cli,
)
from apps.cli.adapters.driving.run_worker import run_worker_cli
from apps.cli.adapters.driving.show_version import show_version
from apps.cli.module import CliModule


def run() -> None:
    if not sys.argv[1:] or sys.argv[1:] in (["--help"], ["-h"]):
        parser = argparse.ArgumentParser(prog="paper-radar", description="Local research workspace commands")
        parser.add_argument(
            "command",
            choices=(
                "version",
                "init",
                "domains",
                "profile",
                "digest",
                "harvest",
                "effects",
                "run-worker",
            ),
            nargs="?",
        )
        parser.print_help()
        return
    if sys.argv[1:2] == ["init"]:
        parser = argparse.ArgumentParser(prog="paper-radar init", allow_abbrev=False)
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument(
            "--workspace",
            help="Dedicated local workspace directory",
        )
        source.add_argument(
            "--env-file",
            help=(
                "Owner-only worker .env file. Init uses "
                "PAPER_RADAR_WORKSPACE and installs the current runtime schema."
            ),
        )
        schema = parser.add_mutually_exclusive_group()
        schema.add_argument(
            "--with-profiles",
            action="store_true",
            help="Explicitly install schemas 0001 and 0002",
        )
        schema.add_argument(
            "--with-discovery",
            action="store_true",
            help="Explicitly install schemas 0001 through 0004",
        )
        schema.add_argument(
            "--with-runtime",
            action="store_true",
            help="Explicitly install the current runtime schema bundle",
        )
        arguments = parser.parse_args(sys.argv[2:])
        workspace = arguments.workspace
        with_runtime = arguments.with_runtime
        if arguments.env_file is not None:
            if (
                arguments.with_profiles
                or arguments.with_discovery
                or arguments.with_runtime
            ):
                parser.error(
                    "--env-file implies the current runtime schema and cannot "
                    "be combined with --with-* schema flags"
                )
            try:
                values = read_worker_env_file(arguments.env_file)
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
            workspace = values.get("PAPER_RADAR_WORKSPACE")
            if not isinstance(workspace, str):
                parser.error(
                    "PAPER_RADAR_WORKSPACE is required in --env-file"
                )
            with_runtime = True
        assert isinstance(workspace, str)
        initialize_workspace(
            workspace,
            with_profiles=arguments.with_profiles,
            with_discovery=arguments.with_discovery,
            with_runtime=with_runtime,
        )
        return
    if sys.argv[1:2] in (["domains"], ["profile"]):
        run_watch_cli(sys.argv[1:])
        return
    if sys.argv[1:2] == ["digest"]:
        run_digest_cli(sys.argv[1:])
        return
    if sys.argv[1:2] == ["harvest"]:
        run_harvest_cli(sys.argv[1:])
        return
    if sys.argv[1:2] == ["effects"]:
        run_workspace_effects_cli(sys.argv[1:])
        return
    if sys.argv[1:2] == ["run-worker"]:
        run_worker_cli(sys.argv[1:])
        return
    injector = Injector([CliModule()], auto_bind=False)
    Fire({"version": partial(show_version, injector)})
