import argparse
import json
import re
import sys
from dataclasses import asdict

from injector import Injector

from apps.cli.exceptions.configuration_file_error import ConfigurationFileError
from apps.cli.helpers.read_configuration_file import read_configuration_file
from apps.cli.module import WatchProfileCliModule
from libs.kernel.exceptions.storage_error import StorageError
from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError
from libs.watch_profiles.ports.import_domain_seeds_port import ImportDomainSeedsPort
from libs.watch_profiles.ports.publish_watch_profile_port import PublishWatchProfilePort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort
from libs.watch_profiles.ports.set_watch_profile_lifecycle_port import SetWatchProfileLifecyclePort


def _revision(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,19}", value) or not 1 <= int(value) < 2**63:
        raise argparse.ArgumentTypeError("revision must be an integer between 1 and 9223372036854775807")
    return int(value)


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise argparse.ArgumentTypeError("id must be a lowercase identifier of at most 64 characters")
    return value


def _parser(group: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"paper-radar {group}", allow_abbrev=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    names = ("import",) if group == "domains" else ("publish", "show", "pause", "resume")
    for name in names:
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--workspace", required=True)
        if name in {"import", "publish"}:
            command.add_argument("--file", required=True, help="Bounded UTF-8 JSON input file")
        else:
            command.add_argument("--id", dest="profile_id", required=True, type=_identifier)
        if name == "publish":
            command.add_argument("--expected-revision", type=_revision, default=None)
        if name == "show":
            command.add_argument("--revision", type=_revision, default=None)
    return parser


def _present(info: ProfileRevision) -> dict[str, object]:
    result = asdict(info)
    result["filters"] = json.loads(result.pop("filters_json"))
    result["domains"] = [{"id": item[0], "revision": item[1]} for item in info.domains]
    return result


def run_watch_cli(argv: list[str]) -> None:
    parser = _parser(argv[0])
    arguments = parser.parse_args(argv[1:])
    if not arguments.workspace.strip() or "\x00" in arguments.workspace:
        parser.error("workspace must be a non-empty path")
    try:
        payload = (
            read_configuration_file(arguments.file) if arguments.operation in {"import", "publish"} else None
        )
        injector = Injector([WatchProfileCliModule(arguments.workspace)], auto_bind=False)
        output: object
        if arguments.operation == "import":
            assert payload is not None
            output = {"domains": [asdict(item) for item in injector.get(ImportDomainSeedsPort)(payload)]}
        elif arguments.operation == "publish":
            assert payload is not None
            output = _present(
                injector.get(PublishWatchProfilePort)(payload, expected_revision=arguments.expected_revision)
            )
        elif arguments.operation == "show":
            output = _present(injector.get(ReadWatchProfilePort)(arguments.profile_id, arguments.revision))
        else:
            lifecycle = "paused" if arguments.operation == "pause" else "active"
            injector.get(SetWatchProfileLifecyclePort)(arguments.profile_id, lifecycle)
            # This acknowledges the committed command, not a later concurrent read.
            output = {"profile_id": arguments.profile_id, "lifecycle_requested": lifecycle, "committed": True}
    except (ConfigurationFileError, WatchConfigurationError, StorageError) as exc:
        error = {"code": exc.code}
        if exc.code == "schema_upgrade_required":
            error["hint"] = "Run init --workspace PATH --with-profiles explicitly before profile operations."
        elif exc.code == "workspace_missing":
            error["hint"] = "Initialize the intended workspace explicitly; this command does not create one."
        print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError):
        # Do not echo the configuration payload or user-provided values into error logs.
        print(json.dumps({"error": {"code": "configuration_io_error"}}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
