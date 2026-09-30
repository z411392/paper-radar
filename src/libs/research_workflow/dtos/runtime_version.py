from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeVersion:
    package_name: str
    package_version: str
    python_version: str
