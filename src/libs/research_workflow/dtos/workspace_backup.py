from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceBackupResult:
    run_id: str
    relative_directory: str
    database_sha256: str
    manifest_sha256: str
    object_count: int
    state: str
