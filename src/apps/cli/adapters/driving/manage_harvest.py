import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from injector import Injector

from apps.cli.module import HarvestPlanCliModule, HarvestRunCliModule
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.discovery.ports.compile_source_query_port import CompileSourceQueryPort
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.run_harvest_slice_port import RunHarvestSlicePort
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
    if moment.tzinfo is None or moment.utcoffset() is None or moment.second != 0 or moment.microsecond != 0:
        raise argparse.ArgumentTypeError("timestamp must include timezone and minute precision")
    return moment


def _bounded_integer(value: str, maximum: int, label: str) -> int:
    if not re.fullmatch(r"[0-9]{1,5}", value):
        raise argparse.ArgumentTypeError(f"{label} must be an integer from 1 to {maximum}")
    number = int(value)
    if not 1 <= number <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be an integer from 1 to {maximum}")
    return number


def _page_size(value: str) -> int:
    return _bounded_integer(value, 2000, "page size")


def _max_pages(value: str) -> int:
    return _bounded_integer(value, 100, "max pages")


def _add_query_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--profile-id", required=True, type=_identifier)
    parser.add_argument("--domain-id", required=True, type=_identifier)
    parser.add_argument("--window-start", required=True, type=_instant)
    parser.add_argument("--window-end", required=True, type=_instant)
    parser.add_argument("--page-size", type=_page_size, default=200)
    parser.add_argument(
        "--defer-unsupported",
        action="store_true",
        help="Keep source-unsupported filters for later pipeline stages instead of rejecting them.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar harvest", allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    plan = commands.add_parser("plan", allow_abbrev=False)
    _add_query_arguments(plan)
    run = commands.add_parser("run", allow_abbrev=False)
    _add_query_arguments(run)
    run.add_argument("--max-pages", type=_max_pages, default=10)
    run.add_argument("--retry-failed", action="store_true")
    run.add_argument("--allow-live-source", action="store_true")
    run.add_argument(
        "--rate-limit-state",
        help="Absolute path to the shared local arXiv rate-limit state file.",
    )
    return parser


def _request(arguments: argparse.Namespace) -> HarvestQueryRequest:
    return HarvestQueryRequest(
        arguments.profile_id,
        arguments.domain_id,
        arguments.window_start,
        arguments.window_end,
        deferred_mode="defer" if arguments.defer_unsupported else "reject",
        page_size=arguments.page_size,
    )


def _error(code: str, *, hint: str | None = None) -> NoReturn:
    error = {"code": code}
    if hint is not None:
        error["hint"] = hint
    print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def run_harvest_cli(argv: list[str]) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\x00" in arguments.workspace:
        parser.error("workspace must be a non-empty path")

    if arguments.operation == "run":
        if not arguments.allow_live_source:
            _error("live_source_not_authorized")
        if arguments.rate_limit_state is None:
            parser.error("--rate-limit-state is required with --allow-live-source")
        if "\x00" in arguments.rate_limit_state or not Path(arguments.rate_limit_state).is_absolute():
            parser.error("--rate-limit-state must be an absolute path without NUL characters")

    try:
        module = (
            HarvestPlanCliModule(arguments.workspace)
            if arguments.operation == "plan"
            else HarvestRunCliModule(arguments.workspace, arguments.rate_limit_state)
        )
        injector = Injector([module], auto_bind=False)
        query = injector.get(BuildHarvestQueryInputPort)(_request(arguments))
        if arguments.operation == "plan":
            output = injector.get(CompileSourceQueryPort)(query)
        else:
            output = injector.get(RunHarvestSlicePort)(
                query,
                max_pages=arguments.max_pages,
                retry_failed=arguments.retry_failed,
            )
    except (
        StorageError,
        WatchConfigurationError,
        HarvestWorkflowError,
        HarvestError,
        SourceFetchError,
        SourceQueryError,
    ) as exc:
        hint = None
        if exc.code == "schema_upgrade_required":
            hint = "Run init --workspace PATH --with-discovery explicitly before harvest operations."
        elif exc.code == "workspace_missing":
            hint = "Initialize the intended workspace explicitly; harvest never creates one."
        _error(exc.code, hint=hint)
    print(json.dumps(asdict(output), ensure_ascii=False, sort_keys=True))
