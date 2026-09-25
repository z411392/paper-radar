import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Any

from injector import Injector

from apps.cli.module import DeliverySubscriptionCliModule
from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
    DeliverySubscription,
)
from libs.delivery.exceptions.delivery_subscription_error import (
    DeliverySubscriptionError,
)
from libs.delivery.ports.configure_delivery_subscription_port import (
    ConfigureDeliverySubscriptionPort,
)
from libs.delivery.ports.read_delivery_subscription_port import (
    ReadDeliverySubscriptionPort,
)
from libs.kernel.exceptions.storage_error import StorageError


def _max_items(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "max items must be an integer from 1 to 100"
        ) from None
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError(
            "max items must be an integer from 1 to 100"
        )
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-radar delivery",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    configure = commands.add_parser("configure", allow_abbrev=False)
    configure.add_argument("--workspace", required=True)
    configure.add_argument("--reader-id", required=True)
    configure.add_argument("--timezone", required=True)
    configure.add_argument("--local-time", required=True)
    configure.add_argument("--max-items", type=_max_items, required=True)
    configure.add_argument("--recipient-ref", required=True)
    lifecycle = configure.add_mutually_exclusive_group(required=True)
    lifecycle.add_argument("--enabled", action="store_true", dest="enabled")
    lifecycle.add_argument("--disabled", action="store_false", dest="enabled")

    show = commands.add_parser("show", allow_abbrev=False)
    show.add_argument("--workspace", required=True)
    show.add_argument("--reader-id", required=True)
    return parser


def _present(value: DeliverySubscription) -> dict[str, Any]:
    result = asdict(value)
    created = result["created_at"]
    assert isinstance(created, datetime)
    result["created_at"] = created.isoformat()
    return result


def _error(code: str, hint: str | None = None) -> None:
    value = {"code": code}
    if hint is not None:
        value["hint"] = hint
    print(json.dumps({"error": value}, ensure_ascii=False), file=sys.stderr)


def run_delivery_cli(argv: list[str]) -> None:
    parser = _parser()
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\0" in arguments.workspace:
        parser.error("workspace must be a non-empty path")

    try:
        injector = Injector(
            [DeliverySubscriptionCliModule(arguments.workspace)],
            auto_bind=False,
        )
        if arguments.operation == "configure":
            result = injector.get(ConfigureDeliverySubscriptionPort)(
                ConfigureDeliverySubscriptionRequest(
                    reader_id=arguments.reader_id,
                    timezone=arguments.timezone,
                    local_time=arguments.local_time,
                    max_items=arguments.max_items,
                    recipient_ref=arguments.recipient_ref,
                    enabled=arguments.enabled,
                )
            )
            output: object = {"subscription": _present(result)}
        else:
            result = injector.get(ReadDeliverySubscriptionPort)(
                arguments.reader_id
            )
            output = {
                "subscription": None if result is None else _present(result)
            }
    except (DeliverySubscriptionError, StorageError) as exc:
        hint = None
        if exc.code == "schema_upgrade_required":
            hint = (
                "Run init --workspace PATH --with-runtime explicitly before "
                "delivery subscription operations."
            )
        elif exc.code == "workspace_missing":
            hint = (
                "Initialize the intended workspace explicitly; delivery "
                "configuration never creates one."
            )
        _error(exc.code, hint)
        raise SystemExit(1) from None

    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
