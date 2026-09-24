import argparse
import json
import re
import signal
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from types import FrameType
from typing import Any
from uuid import uuid4

from injector import Injector

from apps.cli.module import WorkerCliModule
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort


def _bounded_integer(value: str, maximum: int, label: str) -> int:
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise argparse.ArgumentTypeError(f"{label} must be an integer from 1 to {maximum}")
    number = int(value)
    if not 1 <= number <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be an integer from 1 to {maximum}")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar run-worker", allow_abbrev=False)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--poll-seconds",
        type=lambda value: _bounded_integer(value, 3600, "poll seconds"),
        default=60,
    )
    parser.add_argument(
        "--max-new-jobs",
        type=lambda value: _bounded_integer(value, 1000, "max new jobs"),
        default=20,
    )
    parser.add_argument(
        "--max-jobs",
        type=lambda value: _bounded_integer(value, 1000, "max jobs"),
        default=20,
    )
    parser.add_argument(
        "--lease-seconds",
        type=lambda value: _bounded_integer(value, 86400, "lease seconds"),
        default=300,
    )
    parser.add_argument("--allow-live-source", action="store_true")
    parser.add_argument(
        "--rate-limit-state",
        help="Absolute path to the shared local arXiv rate-limit state file.",
    )
    parser.add_argument(
        "--ncbi-email",
        help="Contact email sent to NCBI E-utilities when PubMed live access is commissioned.",
    )
    parser.add_argument(
        "--ncbi-api-key",
        help="Optional NCBI API key; never printed by the worker.",
    )
    parser.add_argument(
        "--ncbi-rate-limit-state",
        help="Absolute path to the shared local NCBI rate-limit state file.",
    )
    return parser


def _error(code: str, hint: str | None = None) -> None:
    value = {"code": code}
    if hint is not None:
        value["hint"] = hint
    print(json.dumps({"error": value}, ensure_ascii=False), file=sys.stderr)


def _cycle(
    command: RunWorkerCyclePort,
    owner_id: str,
    arguments: argparse.Namespace,
) -> dict:
    result = command(
        owner_id,
        max_new_jobs=arguments.max_new_jobs,
        max_jobs=arguments.max_jobs,
        lease_seconds=arguments.lease_seconds,
    )
    return asdict(result)


def _install_stop_handlers(stop: threading.Event) -> dict[int, Any]:
    previous: dict[int, Any] = {}

    def request_stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    return previous


def _restore_handlers(previous: dict[int, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def run_worker_cli(argv: list[str]) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\0" in arguments.workspace:
        parser.error("workspace must be a non-empty path")
    if arguments.rate_limit_state is not None:
        if not arguments.allow_live_source:
            parser.error("--rate-limit-state requires --allow-live-source")
        if "\0" in arguments.rate_limit_state or not Path(arguments.rate_limit_state).is_absolute():
            parser.error("--rate-limit-state must be an absolute path without NUL characters")

    ncbi_values = (
        arguments.ncbi_email,
        arguments.ncbi_api_key,
        arguments.ncbi_rate_limit_state,
    )
    if any(value is not None for value in ncbi_values) and not arguments.allow_live_source:
        parser.error("NCBI options require --allow-live-source")
    if arguments.ncbi_email is None:
        if arguments.ncbi_api_key is not None or arguments.ncbi_rate_limit_state is not None:
            parser.error("--ncbi-email is required for NCBI options")
    else:
        if (
            not arguments.ncbi_email.strip()
            or "\0" in arguments.ncbi_email
            or arguments.ncbi_email.count("@") != 1
            or any(char.isspace() for char in arguments.ncbi_email)
        ):
            parser.error("--ncbi-email must be a single non-whitespace email value")
        if arguments.ncbi_rate_limit_state is None:
            parser.error("--ncbi-rate-limit-state is required with --ncbi-email")
        if (
            "\0" in arguments.ncbi_rate_limit_state
            or not Path(arguments.ncbi_rate_limit_state).is_absolute()
        ):
            parser.error("--ncbi-rate-limit-state must be an absolute path without NUL characters")
        if arguments.ncbi_api_key is not None and (
            not arguments.ncbi_api_key
            or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in arguments.ncbi_api_key)
        ):
            parser.error("--ncbi-api-key must be a non-whitespace token")

    owner_id = "worker:" + uuid4().hex
    try:
        injector = Injector(
            [
                WorkerCliModule(
                    arguments.workspace,
                    allow_live_source=arguments.allow_live_source,
                    rate_limit_state=arguments.rate_limit_state,
                    ncbi_email=arguments.ncbi_email,
                    ncbi_api_key=arguments.ncbi_api_key,
                    ncbi_rate_limit_state=arguments.ncbi_rate_limit_state,
                )
            ],
            auto_bind=False,
        )
        command = injector.get(RunWorkerCyclePort)
        if arguments.once:
            print(json.dumps(_cycle(command, owner_id, arguments), ensure_ascii=False, sort_keys=True))
            return

        stop = threading.Event()
        previous = _install_stop_handlers(stop)
        try:
            while not stop.is_set():
                output = _cycle(command, owner_id, arguments)
                print(json.dumps(output, ensure_ascii=False, sort_keys=True), flush=True)
                stop.wait(arguments.poll_seconds)
        finally:
            _restore_handlers(previous)
    except StorageError as exc:
        hint = None
        if exc.code == "schema_upgrade_required":
            hint = "Run init --workspace PATH --with-runtime explicitly before run-worker."
        elif exc.code == "workspace_missing":
            hint = "Initialize the intended workspace explicitly; run-worker never creates one."
        _error(exc.code, hint)
        raise SystemExit(1) from None
    except WorkflowJobError as exc:
        _error(str(exc))
        raise SystemExit(1) from None
    except (SourceQueryError, SourceFetchError) as exc:
        _error(exc.code)
        raise SystemExit(1) from None
