import argparse
import json
import sys
from dataclasses import asdict

from injector import Injector

from apps.cli.module import ReadingHistoryCliModule
from libs.delivery.exceptions.local_reading_history_error import (
    LocalReadingHistoryError,
)
from libs.delivery.ports.read_local_reading_history_port import (
    ReadLocalReadingHistoryPort,
)
from libs.kernel.exceptions.storage_error import StorageError
from libs.scholarly_catalog.exceptions.local_paper_history_error import (
    LocalPaperHistoryError,
)


def _identifier(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or "\0" in value
        or len(value.encode("utf-8")) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise argparse.ArgumentTypeError("identifier must be a bounded non-empty value")
    return value


def run_reading_history_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="paper-radar reading",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    show = commands.add_parser("show", allow_abbrev=False)
    show.add_argument("--workspace", required=True)
    show.add_argument("--work-id", required=True, type=_identifier)
    show.add_argument("--reader-id", required=True, type=_identifier)
    show.add_argument("--channel", choices=("email", "rss"), default=None)
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\0" in arguments.workspace:
        parser.error("workspace must be a non-empty path")

    try:
        injector = Injector(
            [ReadingHistoryCliModule(arguments.workspace)],
            auto_bind=False,
        )
        result = injector.get(ReadLocalReadingHistoryPort)(
            arguments.work_id,
            arguments.reader_id,
            arguments.channel,
        )
    except (
        LocalPaperHistoryError,
        LocalReadingHistoryError,
        StorageError,
    ) as exc:
        error: dict[str, str] = {"code": exc.code}
        if exc.code == "schema_upgrade_required":
            error["hint"] = (
                "Run init --workspace PATH --with-runtime explicitly before "
                "reading local history."
            )
        elif exc.code == "workspace_missing":
            error["hint"] = (
                "Initialize the intended workspace explicitly; reading history "
                "never creates one."
            )
        print(
            json.dumps({"error": error}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    print(
        json.dumps(
            asdict(result),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
