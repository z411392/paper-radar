import argparse
import sys
from functools import partial

from fire import Fire
from injector import Injector

from apps.cli.adapters.driving.initialize_workspace import initialize_workspace
from apps.cli.adapters.driving.manage_watch_profiles import run_watch_cli
from apps.cli.adapters.driving.show_version import show_version
from apps.cli.module import CliModule


def run() -> None:
    if not sys.argv[1:] or sys.argv[1:] in (["--help"], ["-h"]):
        parser = argparse.ArgumentParser(prog="paper-radar", description="Local research workspace commands")
        parser.add_argument("command", choices=("version", "init", "domains", "profile"), nargs="?")
        parser.print_help()
        return
    if sys.argv[1:2] == ["init"]:
        parser = argparse.ArgumentParser(prog="paper-radar init", allow_abbrev=False)
        parser.add_argument("--workspace", required=True, help="Dedicated local workspace directory")
        parser.add_argument(
            "--with-profiles", action="store_true", help="Explicitly install schemas 0001 and 0002"
        )
        arguments = parser.parse_args(sys.argv[2:])
        initialize_workspace(arguments.workspace, with_profiles=arguments.with_profiles)
        return
    if sys.argv[1:2] in (["domains"], ["profile"]):
        run_watch_cli(sys.argv[1:])
        return
    injector = Injector([CliModule()], auto_bind=False)
    Fire({"version": partial(show_version, injector)})
