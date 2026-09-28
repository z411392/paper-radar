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

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_configuration_file import read_configuration_file
from apps.cli.helpers.read_secret_file import read_secret_file
from apps.cli.helpers.read_worker_env_file import read_worker_env_file
from apps.cli.model_commissioning import (
    commissioned_openrouter_budget_period,
    commissioned_openrouter_execution_policy_fingerprint,
)
from apps.cli.module import WorkerCliModule
from libs.delivery.exceptions.mail_configuration_error import MailConfigurationError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.kernel.exceptions.storage_error import StorageError
from libs.paper_explanations.dtos.generation_budget_policy import (
    GenerationBudgetPolicy,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort


def _bounded_integer(value: str, maximum: int, label: str) -> int:
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise argparse.ArgumentTypeError(f"{label} must be an integer from 1 to {maximum}")
    number = int(value)
    if not 1 <= number <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be an integer from 1 to {maximum}")
    return number


def _budget_micros(value: str, label: str) -> int:
    if re.fullmatch(r"[0-9]{1,19}", value) is None:
        raise argparse.ArgumentTypeError(
            f"{label} must be an integer from 1 to {2**63 - 1}"
        )
    number = int(value)
    if not 1 <= number < 2**63:
        raise argparse.ArgumentTypeError(
            f"{label} must be an integer from 1 to {2**63 - 1}"
        )
    return number


def _env_boolean(values: dict[str, str], key: str) -> bool:
    value = values.get(key, "false")
    if value not in {"true", "false"}:
        raise ConfigurationFileError("invalid_env_value")
    return value == "true"


def _env_integer(
    values: dict[str, str],
    key: str,
    *,
    maximum: int,
    default: int,
) -> int:
    value = values.get(key)
    if value is None:
        return default
    try:
        return _bounded_integer(value, maximum, key)
    except argparse.ArgumentTypeError:
        raise ConfigurationFileError("invalid_env_value") from None


def _env_budget(values: dict[str, str], key: str) -> int | None:
    value = values.get(key)
    if value is None:
        return None
    try:
        return _budget_micros(value, key)
    except argparse.ArgumentTypeError:
        raise ConfigurationFileError("invalid_env_value") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-radar run-worker", allow_abbrev=False)
    parser.add_argument("--workspace")
    parser.add_argument(
        "--env-file",
        help=(
            "Owner-only worker .env file containing runtime configuration and "
            "secrets. Do not combine with runtime configuration flags."
        ),
    )
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
    parser.add_argument("--allow-live-mail", action="store_true")
    parser.add_argument("--allow-live-model", action="store_true")
    parser.add_argument(
        "--openrouter-api-key-file",
        help="Absolute owner-only file containing the OpenRouter API key; never printed.",
    )
    parser.add_argument(
        "--model-period-limit-micros",
        type=lambda value: _budget_micros(value, "model period limit micros"),
    )
    parser.add_argument(
        "--model-reservation-micros",
        type=lambda value: _budget_micros(value, "model reservation micros"),
    )
    parser.add_argument(
        "--recipient-email",
        help=(
            "Single-recipient MVP email address mapped to recipient:primary. "
            "Mutually exclusive with --recipient-map-file."
        ),
    )
    parser.add_argument(
        "--recipient-map-file",
        help=(
            "Optional advanced multi-recipient mapping file. "
            "Mutually exclusive with --recipient-email."
        ),
    )
    parser.add_argument("--smtp-host")
    parser.add_argument(
        "--smtp-port",
        type=lambda value: _bounded_integer(value, 65535, "SMTP port"),
    )
    parser.add_argument("--smtp-sender")
    parser.add_argument("--smtp-username")
    parser.add_argument(
        "--smtp-password-file",
        help="Absolute owner-only file containing the SMTP password; never printed.",
    )
    parser.add_argument(
        "--rate-limit-state",
        help=(
            "Optional absolute override for the arXiv rate-limit state file; "
            "defaults to WORKSPACE/state/arxiv-rate-limit.json."
        ),
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
    parser.add_argument(
        "--crossref-email",
        help="Contact email used for the Crossref polite pool when commissioned.",
    )
    parser.add_argument(
        "--crossref-rate-limit-dir",
        help="Absolute private directory for shared local Crossref rate-limit state.",
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
    env_model_api_key = None
    env_smtp_password = None
    if arguments.env_file is not None:
        runtime_options = {
            "--workspace",
            "--poll-seconds",
            "--max-new-jobs",
            "--max-jobs",
            "--lease-seconds",
            "--allow-live-source",
            "--allow-live-mail",
            "--allow-live-model",
            "--openrouter-api-key-file",
            "--model-period-limit-micros",
            "--model-reservation-micros",
            "--recipient-email",
            "--recipient-map-file",
            "--smtp-host",
            "--smtp-port",
            "--smtp-sender",
            "--smtp-username",
            "--smtp-password-file",
            "--rate-limit-state",
            "--ncbi-email",
            "--ncbi-api-key",
            "--ncbi-rate-limit-state",
            "--crossref-email",
            "--crossref-rate-limit-dir",
        }
        if any(option in argv[1:] for option in runtime_options):
            parser.error(
                "--env-file cannot be combined with runtime configuration flags"
            )
        try:
            env = read_worker_env_file(arguments.env_file)
            arguments.workspace = env.get("PAPER_RADAR_WORKSPACE")
            arguments.allow_live_source = _env_boolean(
                env,
                "PAPER_RADAR_ALLOW_LIVE_SOURCE",
            )
            arguments.allow_live_model = _env_boolean(
                env,
                "PAPER_RADAR_ALLOW_LIVE_MODEL",
            )
            arguments.allow_live_mail = _env_boolean(
                env,
                "PAPER_RADAR_ALLOW_LIVE_MAIL",
            )
            arguments.poll_seconds = _env_integer(
                env,
                "PAPER_RADAR_POLL_SECONDS",
                maximum=3600,
                default=arguments.poll_seconds,
            )
            arguments.max_new_jobs = _env_integer(
                env,
                "PAPER_RADAR_MAX_NEW_JOBS",
                maximum=1000,
                default=arguments.max_new_jobs,
            )
            arguments.max_jobs = _env_integer(
                env,
                "PAPER_RADAR_MAX_JOBS",
                maximum=1000,
                default=arguments.max_jobs,
            )
            arguments.lease_seconds = _env_integer(
                env,
                "PAPER_RADAR_LEASE_SECONDS",
                maximum=86400,
                default=arguments.lease_seconds,
            )
            arguments.model_period_limit_micros = _env_budget(
                env,
                "PAPER_RADAR_MODEL_PERIOD_LIMIT_MICROS",
            )
            arguments.model_reservation_micros = _env_budget(
                env,
                "PAPER_RADAR_MODEL_RESERVATION_MICROS",
            )
            arguments.recipient_email = env.get(
                "PAPER_RADAR_RECIPIENT_EMAIL"
            )
            arguments.smtp_host = env.get("PAPER_RADAR_SMTP_HOST")
            smtp_port = env.get("PAPER_RADAR_SMTP_PORT")
            arguments.smtp_port = (
                None
                if smtp_port is None
                else _env_integer(
                    env,
                    "PAPER_RADAR_SMTP_PORT",
                    maximum=65535,
                    default=465,
                )
            )
            arguments.smtp_sender = env.get("PAPER_RADAR_SMTP_SENDER")
            arguments.smtp_username = env.get("PAPER_RADAR_SMTP_USERNAME")
            env_model_api_key = env.get("PAPER_RADAR_OPENROUTER_API_KEY")
            env_smtp_password = env.get("PAPER_RADAR_SMTP_PASSWORD")
        except ConfigurationFileError as exc:
            _error(exc.code)
            raise SystemExit(1) from None
        except OSError:
            _error("env_configuration_io_error")
            raise SystemExit(1) from None

    if (
        not isinstance(arguments.workspace, str)
        or not arguments.workspace.strip()
        or "\0" in arguments.workspace
    ):
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

    crossref_values = (
        arguments.crossref_email,
        arguments.crossref_rate_limit_dir,
    )
    if any(value is not None for value in crossref_values) and not arguments.allow_live_source:
        parser.error("Crossref options require --allow-live-source")
    if arguments.crossref_email is None:
        if arguments.crossref_rate_limit_dir is not None:
            parser.error("--crossref-email is required for Crossref options")
    else:
        if (
            not arguments.crossref_email.strip()
            or "\0" in arguments.crossref_email
            or arguments.crossref_email.count("@") != 1
            or any(char.isspace() for char in arguments.crossref_email)
        ):
            parser.error("--crossref-email must be a single non-whitespace email value")
        if arguments.crossref_rate_limit_dir is None:
            parser.error("--crossref-rate-limit-dir is required with --crossref-email")
        if (
            "\0" in arguments.crossref_rate_limit_dir
            or not Path(arguments.crossref_rate_limit_dir).is_absolute()
        ):
            parser.error(
                "--crossref-rate-limit-dir must be an absolute path without NUL characters"
            )

    model_credential_sources = sum(
        value is not None
        for value in (
            arguments.openrouter_api_key_file,
            env_model_api_key,
        )
    )
    model_values = (
        arguments.openrouter_api_key_file or env_model_api_key,
        arguments.model_period_limit_micros,
        arguments.model_reservation_micros,
    )
    if any(value is not None for value in model_values) and not arguments.allow_live_model:
        parser.error("model options require --allow-live-model")

    generation_budget_policy = None
    if arguments.allow_live_model:
        if model_credential_sources != 1 or any(
            value is None
            for value in (
                arguments.model_period_limit_micros,
                arguments.model_reservation_micros,
            )
        ):
            parser.error(
                "--allow-live-model requires one credential and complete model budget policy"
            )
        assert arguments.model_period_limit_micros is not None
        assert arguments.model_reservation_micros is not None
        if arguments.openrouter_api_key_file is not None and (
            "\0" in arguments.openrouter_api_key_file
            or not Path(arguments.openrouter_api_key_file).is_absolute()
        ):
            parser.error(
                "--openrouter-api-key-file must be an absolute path without NUL characters"
            )
        if arguments.model_reservation_micros > arguments.model_period_limit_micros:
            parser.error(
                "--model-reservation-micros must not exceed --model-period-limit-micros"
            )
        period_key, currency = commissioned_openrouter_budget_period()
        generation_budget_policy = GenerationBudgetPolicy(
            period_key,
            currency,
            arguments.model_period_limit_micros,
            arguments.model_reservation_micros,
            commissioned_openrouter_execution_policy_fingerprint(),
        )

    mail_values = (
        arguments.recipient_email,
        arguments.recipient_map_file,
        arguments.smtp_host,
        arguments.smtp_port,
        arguments.smtp_sender,
        arguments.smtp_username,
        arguments.smtp_password_file,
        env_smtp_password,
    )
    if any(value is not None for value in mail_values) and not arguments.allow_live_mail:
        parser.error("mail options require --allow-live-mail")
    recipient_map_json = None
    smtp_password = None
    if arguments.allow_live_mail:
        recipient_modes = sum(
            value is not None
            for value in (
                arguments.recipient_email,
                arguments.recipient_map_file,
            )
        )
        if recipient_modes != 1:
            parser.error(
                "--allow-live-mail requires exactly one of --recipient-email "
                "or --recipient-map-file"
            )
        smtp_password_sources = sum(
            value is not None
            for value in (
                arguments.smtp_password_file,
                env_smtp_password,
            )
        )
        smtp_values = (
            arguments.smtp_host,
            arguments.smtp_port,
            arguments.smtp_sender,
            arguments.smtp_username,
        )
        if (
            any(value is None for value in smtp_values)
            or smtp_password_sources != 1
        ):
            parser.error(
                "--allow-live-mail requires SMTP host/port/sender/username "
                "and exactly one password source"
            )
        local_files = []
        if arguments.smtp_password_file is not None:
            local_files.append(
                (arguments.smtp_password_file, "--smtp-password-file")
            )
        if arguments.recipient_map_file is not None:
            local_files.append(
                (arguments.recipient_map_file, "--recipient-map-file")
            )
        for value, label in local_files:
            if "\0" in value or not Path(value).is_absolute():
                parser.error(
                    f"{label} must be an absolute path without NUL characters"
                )
        for value, label in (
            (arguments.smtp_host, "--smtp-host"),
            (arguments.smtp_sender, "--smtp-sender"),
            (arguments.smtp_username, "--smtp-username"),
        ):
            if (
                not isinstance(value, str)
                or not value.strip()
                or "\0" in value
                or "\r" in value
                or "\n" in value
            ):
                parser.error(f"{label} must be a non-empty single-line value")
        try:
            if arguments.recipient_email is not None:
                recipient_map_json = json.dumps(
                    {"recipient:primary": arguments.recipient_email},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            else:
                assert arguments.recipient_map_file is not None
                recipient_map_json = read_configuration_file(
                    arguments.recipient_map_file
                )
            smtp_password = env_smtp_password
            if smtp_password is None:
                assert arguments.smtp_password_file is not None
                smtp_password = read_secret_file(
                    arguments.smtp_password_file
                )
        except ConfigurationFileError as exc:
            _error(str(exc))
            raise SystemExit(1) from None
        except OSError:
            _error("mail_configuration_io_error")
            raise SystemExit(1) from None

    model_api_key = env_model_api_key
    if generation_budget_policy is not None and model_api_key is None:
        assert arguments.openrouter_api_key_file is not None
        try:
            model_api_key = read_secret_file(arguments.openrouter_api_key_file)
        except ConfigurationFileError as exc:
            _error(str(exc))
            raise SystemExit(1) from None
        except OSError:
            _error("model_configuration_io_error")
            raise SystemExit(1) from None

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
                    crossref_email=arguments.crossref_email,
                    crossref_rate_limit_dir=arguments.crossref_rate_limit_dir,
                    allow_live_mail=arguments.allow_live_mail,
                    recipient_map_json=recipient_map_json,
                    smtp_host=arguments.smtp_host,
                    smtp_port=arguments.smtp_port,
                    smtp_sender=arguments.smtp_sender,
                    smtp_username=arguments.smtp_username,
                    smtp_password=smtp_password,
                    allow_live_model=arguments.allow_live_model,
                    model_api_key=model_api_key,
                    generation_budget_policy=generation_budget_policy,
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
    except MailConfigurationError as exc:
        _error(exc.code)
        raise SystemExit(1) from None
    except ModelGatewayError as exc:
        _error(exc.code)
        raise SystemExit(1) from None
