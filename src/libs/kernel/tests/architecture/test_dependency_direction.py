import ast
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class Violation:
    path: str
    code: str
    target: str = ""


# The executable projection of the approved Context Map, not a runtime registry.
ALLOWED_EDGES = {
    "research_workflow": {
        "delivery",
        "discovery",
        "watch_profiles",
        "scholarly_catalog",
        "paper_explanations",
        "retrieval",
    },
    "delivery": {"scholarly_catalog", "paper_explanations", "watch_profiles"},
    "watch_profiles": {"retrieval"},
    "retrieval": {"scholarly_catalog", "paper_explanations"},
    "paper_explanations": {"scholarly_catalog"},
    "scholarly_catalog": {"discovery"},
    "discovery": set(),
    "kernel": set(),
}
IO_MODULES = {
    "sqlite3",
    "pathlib",
    "os",
    "sys",
    "socket",
    "subprocess",
    "http",
    "httpx",
    "requests",
    "urllib",
    "smtplib",
    "faiss",
    "importlib",
}
FORBIDDEN_DIRECTORIES = {"contracts", "types", "public", "shared"}


def dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def imports_for(tree: ast.Module, module: str) -> tuple[list[str], dict[str, str]]:
    imports: list[str] = []
    aliases: dict[str, str] = {}
    package = module.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                key = alias.asname or alias.name.split(".")[0]
                aliases[key] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package[: len(package) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            for alias in node.names:
                target = f"{base}.{alias.name}" if base else alias.name
                imports.append(target)
                aliases[alias.asname or alias.name] = target
    return imports, aliases


def is_inbound_port(root: Path, target: str) -> bool:
    parts = target.split(".")
    while len(parts) >= 4:
        path = root.joinpath(*parts).with_suffix(".py")
        if path.is_file():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                return False
            return any(
                isinstance(node, ast.ClassDef)
                and any(
                    isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name == "__call__"
                    for method in node.body
                )
                for node in tree.body
            )
        parts.pop()
    return False


def graph_has_cycle(graph: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> bool:
        if name in visiting:
            return True
        if name in visited:
            return False
        visiting.add(name)
        if any(visit(target) for target in sorted(graph.get(name, set()))):
            return True
        visiting.remove(name)
        visited.add(name)
        return False

    return any(visit(name) for name in sorted(graph))


def scan_sources(root: Path) -> list[Violation]:
    errors: list[Violation] = []
    graph: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        parts = relative.parts
        if path.name == "__init__.py":
            errors.append(Violation(str(relative), "invalid_layout"))
        if "tests" in parts:
            continue
        if len(parts) < 3 or parts[0] not in {"apps", "libs"}:
            errors.append(Violation(str(relative), "invalid_layout"))
            continue
        is_app = parts[0] == "apps"
        owner = parts[1]
        layer = parts[2] if len(parts) > 3 else ""
        if (
            any(part in FORBIDDEN_DIRECTORIES for part in parts[2:])
            or path.name == "utils.py"
            or (
                is_app
                and (
                    any(part in {"ports", "application", "domain"} for part in parts[2:])
                    or path.name in {"service.py", "ports.py", "composition.py", "errors.py"}
                )
            )
            or (is_app and "adapters/driven" in relative.as_posix())
        ):
            errors.append(Violation(str(relative), "invalid_layout"))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            errors.append(Violation(str(relative), "invalid_syntax"))
            continue
        imports, aliases = imports_for(tree, ".".join(relative.with_suffix("").parts))
        composition = is_app and parts[2:] == ("module.py",)
        for target in imports:
            dest = target.split(".")
            if not is_app and layer in {"application", "domain"} and dest[0] in IO_MODULES:
                errors.append(Violation(str(relative), "inner_layer_io", target))
            if dest[0] not in {"apps", "libs"}:
                continue
            if "*" in dest:
                errors.append(Violation(str(relative), "wildcard_project_import", target))
                continue
            if len(dest) < 3:
                errors.append(Violation(str(relative), "ambiguous_project_import", target))
                continue
            if dest[0] == "apps":
                if not is_app:
                    errors.append(Violation(str(relative), "lib_to_app", target))
                elif dest[1] != owner:
                    errors.append(Violation(str(relative), "cross_app", target))
                continue
            if is_app:
                if composition:
                    continue
                if dest[2] not in {"ports", "dtos", "exceptions"}:
                    errors.append(Violation(str(relative), "driver_to_implementation", target))
                elif dest[2] == "ports" and not is_inbound_port(root, target):
                    errors.append(Violation(str(relative), "driver_to_outbound_port", target))
                continue
            if layer in {"application", "domain", "ports", "dtos"} and dest[2] == "adapters":
                errors.append(Violation(str(relative), "inner_to_adapter", target))
            if owner == "scholarly_catalog" and dest[1] == "discovery" and dest[2] != "dtos":
                errors.append(Violation(str(relative), "catalog_discovery_dto_only", target))
            if owner != dest[1]:
                graph.setdefault(owner, set()).add(dest[1])
                if owner == "kernel":
                    errors.append(Violation(str(relative), "kernel_to_feature", target))
                if dest[1] != "kernel":
                    if dest[2] not in {"ports", "dtos"}:
                        errors.append(Violation(str(relative), "private_cross_feature", target))
                    if dest[1] not in ALLOWED_EDGES.get(owner, set()):
                        errors.append(Violation(str(relative), "unapproved_feature_edge", target))
                continue
            if layer == "domain" and dest[2] == "application":
                errors.append(Violation(str(relative), "domain_to_application", target))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            head, _, tail = name.partition(".")
            resolved = aliases.get(head, head) + (f".{tail}" if tail else "")
            if not is_app and layer in {"application", "domain"} and resolved in {"open", "builtins.open"}:
                errors.append(Violation(str(relative), "inner_layer_io", resolved))
            if resolved in {"__import__", "builtins.__import__", "importlib.import_module"}:
                errors.append(Violation(str(relative), "dynamic_import", resolved))
            if resolved in {"sys.path.insert", "sys.path.append", "sys.path.extend"}:
                errors.append(Violation(str(relative), "runtime_path_mutation", resolved))
    if graph_has_cycle(graph):
        errors.append(Violation("libs", "feature_cycle"))
    return errors


@pytest.mark.parametrize(
    ("path", "code", "expected"),
    [
        (
            "libs/discovery/application/scan.py",
            "from libs.delivery.adapters.driven.store import Store",
            "private_cross_feature",
        ),
        (
            "libs/discovery/domain/paper.py",
            "from libs.delivery.domain.model import Paper",
            "private_cross_feature",
        ),
        (
            "libs/discovery/ports/scan.py",
            "from libs.delivery.application.scan import Scan",
            "private_cross_feature",
        ),
        (
            "libs/kernel/helpers/hash.py",
            "from libs.delivery.dtos.digest import Digest",
            "kernel_to_feature",
        ),
        (
            "libs/discovery/application/scan.py",
            "from apps.cli.dtos.request import Request",
            "lib_to_app",
        ),
        (
            "apps/cli/adapters/driving/scan.py",
            "from libs.discovery.application.scan import Scan",
            "driver_to_implementation",
        ),
        (
            "apps/cli/adapters/driving/scan.py",
            "import libs.discovery.adapters.driven.source as source",
            "driver_to_implementation",
        ),
        (
            "apps/cli/adapters/driving/scan.py",
            "from libs.discovery.ports.out_port import OutPort",
            "driver_to_outbound_port",
        ),
        (
            "apps/cli/module.py",
            "from apps.http.module import HttpModule",
            "cross_app",
        ),
        (
            "libs/discovery/application/scan.py",
            "from libs.kernel.adapters.driven.store import Store",
            "inner_to_adapter",
        ),
        (
            "libs/scholarly_catalog/application/scan.py",
            "from libs.discovery.ports.source import SourcePort",
            "catalog_discovery_dto_only",
        ),
        (
            "libs/discovery/domain/model.py",
            "open('state.json')",
            "inner_layer_io",
        ),
        (
            "libs/discovery/application/scan.py",
            "import sqlite3",
            "inner_layer_io",
        ),
        (
            "libs/discovery/domain/model.py",
            "from pathlib import Path",
            "inner_layer_io",
        ),
        (
            "libs/discovery/application/scan.py",
            "from ..adapters.driven.source import Source",
            "inner_to_adapter",
        ),
        (
            "libs/discovery/domain/model.py",
            "from ..application.scan import Scan",
            "domain_to_application",
        ),
        (
            "libs/discovery/application/scan.py",
            "from libs import delivery",
            "ambiguous_project_import",
        ),
        (
            "libs/discovery/application/scan.py",
            "from libs.discovery.ports import *",
            "wildcard_project_import",
        ),
        (
            "libs/discovery/adapters/driven/source.py",
            "__import__('libs.delivery.adapters.driven.store')",
            "dynamic_import",
        ),
        (
            "libs/discovery/adapters/driven/source.py",
            "import importlib as il; il.import_module('libs.delivery')",
            "dynamic_import",
        ),
        (
            "apps/cli/entrypoints.py",
            "import sys as s; s.path.insert(0, 'src')",
            "runtime_path_mutation",
        ),
        (
            "libs/discovery/application/scan.py",
            "from libs.delivery.ports.digest import DigestPort",
            "unapproved_feature_edge",
        ),
    ],
)
def test_forbidden_import_has_a_specific_diagnostic(
    tmp_path: Path, path: str, code: str, expected: str
) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    port = tmp_path / "libs/discovery/ports/out_port.py"
    port.parent.mkdir(parents=True, exist_ok=True)
    port.write_text("class OutPort:\n    def read(self): pass\n", encoding="utf-8")
    assert expected in {v.code for v in scan_sources(tmp_path)}


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (
            "libs/scholarly_catalog/application/register.py",
            "from libs.discovery.dtos.record import Record",
        ),
        (
            "libs/delivery/application/send.py",
            "from libs.scholarly_catalog.ports.read import ReadPaper",
        ),
        (
            "libs/discovery/application/scan.py",
            "from ..domain.services.plan import Plan",
        ),
        (
            "libs/discovery/adapters/driven/store.py",
            "import sqlite3",
        ),
        (
            "apps/cli/module.py",
            "from libs.discovery.adapters.driven.store import Store",
        ),
        (
            "apps/cli/adapters/driving/run.py",
            "from libs.discovery.dtos.record import Record",
        ),
    ],
)
def test_nearby_legal_imports_are_not_rejected(tmp_path: Path, path: str, code: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    assert scan_sources(tmp_path) == []


def test_driver_may_call_an_inbound_port(tmp_path: Path) -> None:
    port = tmp_path / "libs/discovery/ports/run_port.py"
    port.parent.mkdir(parents=True, exist_ok=True)
    port.write_text("class RunPort:\n    def __call__(self): pass\n", encoding="utf-8")
    driver = tmp_path / "apps/cli/adapters/driving/run.py"
    driver.parent.mkdir(parents=True, exist_ok=True)
    driver.write_text("from libs.discovery.ports.run_port import RunPort\n", encoding="utf-8")
    assert scan_sources(tmp_path) == []


@pytest.mark.parametrize(
    "path",
    [
        "apps/cli/domain/model.py",
        "apps/cli/application/run.py",
        "apps/cli/ports/run_port.py",
        "apps/cli/service.py",
        "apps/cli/composition.py",
        "apps/cli/adapters/driven/store.py",
        "libs/discovery/__init__.py",
        "libs/discovery/shared/state.py",
    ],
)
def test_invalid_layout_is_rejected(tmp_path: Path, path: str) -> None:
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    assert any(v.code == "invalid_layout" for v in scan_sources(tmp_path))


def test_feature_cycle_is_rejected(tmp_path: Path) -> None:
    for owner, dependency in [("delivery", "scholarly_catalog"), ("scholarly_catalog", "delivery")]:
        path = tmp_path / f"libs/{owner}/ports/read_port.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"from libs.{dependency}.dtos.record import Record\n", encoding="utf-8")
    assert any(v.code == "feature_cycle" for v in scan_sources(tmp_path))


def test_syntax_error_cannot_escape_guard(tmp_path: Path) -> None:
    path = tmp_path / "libs/discovery/domain/broken.py"
    path.parent.mkdir(parents=True)
    path.write_text("def broken(:", encoding="utf-8")
    assert any(v.code == "invalid_syntax" for v in scan_sources(tmp_path))


def test_production_source_tree_passes() -> None:
    root = Path(__file__).resolve().parents[4]
    assert (root / "apps/cli/__main__.py").is_file()
    assert scan_sources(root) == []
