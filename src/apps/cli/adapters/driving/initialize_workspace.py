import json
import sys
from dataclasses import asdict

from injector import Injector

from apps.cli.module import CliModule
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort


def initialize_workspace(
    workspace: str,
    *,
    with_profiles: bool = False,
    with_discovery: bool = False,
    with_runtime: bool = False,
) -> None:
    try:
        if not workspace.strip() or "\x00" in workspace:
            raise ValueError("workspace must be a non-empty path")
        injector = Injector(
            [
                CliModule(
                    workspace,
                    with_profiles=with_profiles,
                    with_discovery=with_discovery,
                    with_runtime=with_runtime,
                )
            ],
            auto_bind=False,
        )
        info = injector.get(InitializeWorkspacePort)()
    except StorageError as exc:
        print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}), file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        print(
            json.dumps({"error": {"code": "invalid_workspace_path", "message": str(exc)}}),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except OSError as exc:
        print(json.dumps({"error": {"code": "file_io", "message": str(exc)}}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(asdict(info), sort_keys=True))
