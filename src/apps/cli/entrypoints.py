import argparse
import sys
from functools import partial

from fire import Fire
from injector import Injector

from apps.cli.adapters.driving.initialize_workspace import initialize_workspace
from apps.cli.adapters.driving.show_version import show_version
from apps.cli.module import CliModule


def run() -> None:
    if sys.argv[1:2] == ["init"]:
        # Validate the entire mutating command before constructing any workspace adapter.
        parser = argparse.ArgumentParser(prog="paper-radar init", allow_abbrev=False)
        parser.add_argument("--workspace", required=True, help="Dedicated local workspace directory")
        arguments = parser.parse_args(sys.argv[2:])
        initialize_workspace(arguments.workspace)
        return
    injector = Injector([CliModule()], auto_bind=False)
    Fire({"version": partial(show_version, injector)})
