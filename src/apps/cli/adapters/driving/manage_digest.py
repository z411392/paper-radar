import argparse
import json
import re
import sys
from dataclasses import asdict

from injector import Injector

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_worker_env_file import read_worker_env_file
from apps.cli.module import DeliveryCliModule
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError
from libs.delivery.exceptions.delivery_subscription_error import (
    DeliverySubscriptionError,
)
from libs.delivery.ports.configure_email_subscription_port import (
    ConfigureEmailSubscriptionPort,
)
from libs.delivery.ports.read_delivery_history_port import ReadDeliveryHistoryPort
from libs.kernel.exceptions.storage_error import StorageError


def _reader_id(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
        raise argparse.ArgumentTypeError(
            "reader id must be a lowercase identifier of at most 64 characters"
        )
    return value


def _recipient_ref(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is None:
        raise argparse.ArgumentTypeError(
            "recipient ref must be a bounded identifier"
        )
    return value


def _local_time(value: str) -> str:
    if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is None:
        raise argparse.ArgumentTypeError(
            "local time must use 24-hour HH:MM format"
        )
    return value


def _limit(value: str) -> int:
    if re.fullmatch(r"[0-9]{1,3}", value) is None:
        raise argparse.ArgumentTypeError(
            "limit must be an integer from 1 to 100"
        )
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError(
            "limit must be an integer from 1 to 100"
        )
    return number


def _max_items(value: str) -> int:
    if re.fullmatch(r"[0-9]{1,3}", value) is None:
        raise argparse.ArgumentTypeError(
            "max items must be an integer from 1 to 100"
        )
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError(
            "max items must be an integer from 1 to 100"
        )
    return number


def run_digest_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="paper-radar digest",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    subscribe = commands.add_parser(
        "subscribe-email",
        allow_abbrev=False,
    )
    subscribe.add_argument("--workspace", required=True)
    subscribe.add_argument("--reader-id", required=True, type=_reader_id)
    subscribe.add_argument(
        "--recipient-ref",
        required=True,
        type=_recipient_ref,
    )
    subscribe.add_argument("--timezone", required=True)
    subscribe.add_argument(
        "--local-time",
        required=True,
        type=_local_time,
    )
    subscribe.add_argument(
        "--max-items",
        type=_max_items,
        default=5,
    )
    status = commands.add_parser(
        "status",
        allow_abbrev=False,
    )
    source = status.add_mutually_exclusive_group(required=True)
    source.add_argument("--workspace")
    source.add_argument("--env-file")
    status.add_argument(
        "--reader-id",
        default="local",
        type=_reader_id,
    )
    status.add_argument(
        "--limit",
        type=_limit,
        default=10,
    )

    arguments = parser.parse_args(argv[1:])
    workspace = getattr(arguments, "workspace", None)
    if (
        arguments.operation == "status"
        and arguments.env_file is not None
    ):
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

    if (
        not isinstance(workspace, str)
        or not workspace.strip()
        or "\0" in workspace
    ):
        parser.error("workspace must be a non-empty path")

    if arguments.operation == "subscribe-email" and (
        not arguments.timezone.strip()
        or "\0" in arguments.timezone
        or len(arguments.timezone) > 128
    ):
        parser.error("timezone must be a non-empty timezone name")

    try:
        injector = Injector(
            [DeliveryCliModule(workspace)],
            auto_bind=False,
        )
        if arguments.operation == "subscribe-email":
            result = injector.get(ConfigureEmailSubscriptionPort)(
                arguments.reader_id,
                arguments.recipient_ref,
                arguments.timezone,
                arguments.local_time,
                max_items=arguments.max_items,
            )
            output = asdict(result)
        else:
            history = injector.get(ReadDeliveryHistoryPort)(
                arguments.reader_id,
                limit=arguments.limit,
            )
            output = {
                "deliveries": [
                    asdict(item)
                    for item in history
                ]
            }
    except (DeliverySubscriptionError, DeliveryStoreError) as exc:
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not code:
            code = str(exc) or exc.__class__.__name__
        print(
            json.dumps(
                {"error": {"code": code}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except StorageError as exc:
        error: dict[str, str] = {"code": exc.code}
        if exc.code == "schema_upgrade_required":
            error["hint"] = (
                "Run init --env-file FILE or legacy "
                "init --workspace PATH --with-runtime first."
            )
        elif exc.code == "workspace_missing":
            error["hint"] = (
                "Initialize the intended workspace explicitly; digest never "
                "creates one."
            )
        print(
            json.dumps({"error": error}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    print(
        json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
