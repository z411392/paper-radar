import argparse
import json
import sys
from dataclasses import asdict

from injector import Injector

from apps.cli.module import OperationalHealthCliModule
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)
from libs.research_workflow.ports.operational_health_port import InspectHealthPort


def run_health_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="paper-radar health",
        allow_abbrev=False,
    )
    parser.add_argument("--workspace", required=True)
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\0" in arguments.workspace:
        parser.error("workspace must be a non-empty path")

    try:
        injector = Injector(
            [OperationalHealthCliModule(arguments.workspace)],
            auto_bind=False,
        )
        result = injector.get(InspectHealthPort)()
    except (StorageError, OperationalHealthError) as exc:
        code = getattr(exc, "code", str(exc))
        print(
            json.dumps({"error": {"code": code}}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
