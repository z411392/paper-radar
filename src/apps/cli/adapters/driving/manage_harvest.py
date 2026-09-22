import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime

from injector import Injector

from apps.cli.module import HarvestPlanCliModule
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.discovery.ports.compile_source_query_port import CompileSourceQueryPort
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError


def _identifier(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
        raise argparse.ArgumentTypeError("expected a lowercase identifier of at most 64 characters")
    return value


def _instant(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("expected an ISO-8601 timestamp with timezone") from None
    if (
        moment.tzinfo is None
        or moment.utcoffset() is None
        or moment.second != 0
        or moment.microsecond != 0
    ):
        raise argparse.ArgumentTypeError("timestamp must include timezone and minute precision")
    return moment


def _page_size(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,4}", value):
        raise argparse.ArgumentTypeError("page size must be an integer from 1 to 2000")
    number = int(value)
    if not 1 <= number <= 2000:
        raise argparse.ArgumentTypeError("page size must be an integer from 1 to 2000")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar harvest", allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    plan = commands.add_parser("plan", allow_abbrev=False)
    plan.add_argument("--workspace", required=True)
    plan.add_argument("--profile-id", required=True, type=_identifier)
    plan.add_argument("--domain-id", required=True, type=_identifier)
    plan.add_argument("--window-start", required=True, type=_instant)
    plan.add_argument("--window-end", required=True, type=_instant)
    plan.add_argument("--page-size", type=_page_size, default=200)
    plan.add_argument(
        "--defer-unsupported",
        action="store_true",
        help="Keep source-unsupported filters for later pipeline stages instead of rejecting them.",
    )
    return parser


def run_harvest_cli(argv: list[str]) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\x00" in arguments.workspace:
        parser.error("workspace must be a non-empty path")
    try:
        injector = Injector([HarvestPlanCliModule(arguments.workspace)], auto_bind=False)
        request = HarvestQueryRequest(
            arguments.profile_id,
            arguments.domain_id,
            arguments.window_start,
            arguments.window_end,
            deferred_mode="defer" if arguments.defer_unsupported else "reject",
            page_size=arguments.page_size,
        )
        query = injector.get(BuildHarvestQueryInputPort)(request)
        plan = injector.get(CompileSourceQueryPort)(query)
    except (StorageError, WatchConfigurationError, HarvestWorkflowError, SourceQueryError) as exc:
        error = {"code": exc.code}
        if exc.code == "schema_upgrade_required":
            error["hint"] = (
                "Run init --workspace PATH --with-discovery explicitly before harvest operations."
            )
        elif exc.code == "workspace_missing":
            error["hint"] = "Initialize the intended workspace explicitly; harvest never creates one."
        print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(asdict(plan), ensure_ascii=False, sort_keys=True))
