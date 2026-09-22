from dataclasses import dataclass


@dataclass(frozen=True)
class StorageProblem:
    code: str
    relative_path: str
    object_id: str | None = None


@dataclass(frozen=True)
class StorageReport:
    checked: int
    problems: tuple[StorageProblem, ...]
