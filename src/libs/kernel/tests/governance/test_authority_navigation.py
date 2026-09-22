import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[5]
MANIFEST = ".authority/manifests/git-baseline.json"
NAVIGATION = ".context/navigation.json"


def checked_path(root, value):
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    parts = PurePosixPath(value)
    if parts.is_absolute() or any(part in {"..", "."} for part in value.split("/")):
        return None
    path = root / value
    if not path.resolve().is_relative_to(root.resolve()):
        return None
    if any((root / Path(*parts.parts[:index])).is_symlink() for index in range(1, len(parts.parts) + 1)):
        return None
    return path if path.exists() else None


def manifest_errors(root, data):
    required = {"format_version", "repository_id", "mode", "resolution", "issue_authority_enabled",
                "acceptance_receipts", "git_sources"}
    if not isinstance(data, dict) or set(data) != required:
        return ["manifest_shape"]
    errors = []
    if type(data["format_version"]) is not int or data["format_version"] != 1:
        errors.append("format_version")
    if type(data["repository_id"]) is not int or data["repository_id"] != 1381086277:
        errors.append("repository_identity")
    if data["mode"] != "git-retained" or data["resolution"] != "same-commit-tree":
        errors.append("binding_mode")
    if data["issue_authority_enabled"] is not False or data["acceptance_receipts"] != []:
        errors.append("issue_authority_not_commissioned")
    sources = data["git_sources"]
    if not isinstance(sources, list) or not sources:
        return errors + ["missing_sources"]
    ids, paths = set(), set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"id", "path", "sha256", "source_status"}:
            errors.append("source_shape")
            continue
        identity = source["id"]
        if not isinstance(identity, str) or not re.fullmatch(r"paper-radar:1381086277:[a-z0-9:-]+", identity):
            errors.append("source_identity")
        elif identity in ids:
            errors.append("duplicate_identity")
        else:
            ids.add(identity)
        value = source["path"]
        path = checked_path(root, value)
        if path is None or not path.is_file():
            errors.append("source_path")
            continue
        if not value.startswith(("docs/", "specs/", ".claude/rules/")):
            errors.append("source_scope")
        if value in paths:
            errors.append("duplicate_path")
        paths.add(value)
        if source["source_status"] != "preserve_source_declaration":
            errors.append("unproven_acceptance")
        if source["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append("source_digest")
    return errors


def navigation_errors(root, data):
    keys = {"format_version", "kind", "repository_id", "access", "common", "owners", "entrypoints"}
    if not isinstance(data, dict) or set(data) != keys:
        return ["navigation_shape"]
    errors = []
    if data["format_version"] != 1 or data["kind"] != "navigation_only":
        errors.append("navigation_kind")
    if data["repository_id"] != 1381086277 or data["access"] != "private_repository":
        errors.append("navigation_access")
    owners = data["owners"]
    names = {"watch_profiles", "discovery", "scholarly_catalog", "paper_explanations", "delivery",
             "retrieval", "research_workflow", "kernel"}
    if not isinstance(owners, dict) or set(owners) != names:
        return errors + ["owner_set"]
    values = [data["common"], data["entrypoints"]]
    for name, owner in owners.items():
        if not isinstance(owner, dict) or set(owner) != {"role", "references", "code_and_tests"}:
            errors.append("owner_shape")
            continue
        role = "technical_owner" if name in {"retrieval", "research_workflow", "kernel"} else "business_context"
        if owner["role"] != role:
            errors.append("owner_role")
        values += [owner["references"], owner["code_and_tests"]]
    for group in values:
        if not isinstance(group, list):
            errors.append("path_list")
            continue
        for value in group:
            if checked_path(root, value) is None:
                errors.append("navigation_path")
    return errors


def test_current_git_sources_resolve_without_self_sha_or_fake_acceptance():
    data = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert manifest_errors(ROOT, data) == []
    indexed = {item["path"] for item in data["git_sources"]}
    assert {str(p.relative_to(ROOT)) for p in (ROOT / "specs").glob("*/spec.md")} <= indexed
    assert {str(p.relative_to(ROOT)) for p in (ROOT / ".claude/rules").glob("*.md")} <= indexed


def test_current_navigation_resolves_without_becoming_specs_or_assignment():
    assert navigation_errors(ROOT, json.loads((ROOT / NAVIGATION).read_text(encoding="utf-8"))) == []


@pytest.fixture
def sample(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/rule.md").write_text("scope is a fixture", encoding="utf-8")
    data = {"format_version": 1, "repository_id": 1381086277, "mode": "git-retained",
            "resolution": "same-commit-tree", "issue_authority_enabled": False, "acceptance_receipts": [],
            "git_sources": [{"id": "paper-radar:1381086277:bc:fixture", "path": "docs/rule.md",
                             "sha256": hashlib.sha256((tmp_path / "docs/rule.md").read_bytes()).hexdigest(),
                             "source_status": "preserve_source_declaration"}]}
    assert manifest_errors(tmp_path, data) == []
    return tmp_path, data


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(issue_authority_enabled=True),
    lambda d: d.update(acceptance_receipts=[{"accepted_by": "self-declared"}]),
    lambda d: d.update(code_sha="0" * 40),
    lambda d: d.update(repository_id=1),
    lambda d: d.update(format_version=True),
    lambda d: d.update(resolution="latest-main"),
    lambda d: d.update(git_sources=[]),
    lambda d: d["git_sources"].append(copy.deepcopy(d["git_sources"][0])),
    lambda d: d["git_sources"][0].update(source_status="accepted"),
    lambda d: d["git_sources"][0].update(sha256="0" * 64),
    lambda d: d["git_sources"][0].update(path="../external.md"),
    lambda d: d["git_sources"][0].update(path="/tmp/external.md"),
    lambda d: d["git_sources"][0].update(path="docs/missing.md"),
    lambda d: d["git_sources"][0].update(id="paper-radar:1381086277:docs/rule.md"),
])
def test_invalid_authority_does_not_pass_as_current(sample, mutate):
    root, data = sample
    mutate(data)
    assert manifest_errors(root, data)


def test_semantic_identity_survives_file_rename(sample):
    root, data = sample
    identity = data["git_sources"][0]["id"]
    (root / "docs/rule.md").rename(root / "docs/renamed.md")
    data["git_sources"][0]["path"] = "docs/renamed.md"
    assert data["git_sources"][0]["id"] == identity
    assert manifest_errors(root, data) == []


def test_same_digest_does_not_authorize_a_symlink(sample):
    root, data = sample
    (root / "docs/link.md").symlink_to("rule.md")
    data["git_sources"][0]["path"] = "docs/link.md"
    assert "source_path" in manifest_errors(root, data)


@pytest.mark.parametrize("field", ["status", "priority", "assignee", "accepted_spec", "current_pr"])
def test_navigation_rejects_shadow_authority_or_live_state(field):
    data = json.loads((ROOT / NAVIGATION).read_text(encoding="utf-8"))
    data[field] = "not navigation"
    assert "navigation_shape" in navigation_errors(ROOT, data)


def test_existing_safety_and_history_are_not_relaxed():
    rule = (ROOT / ".claude/rules/15-execution-strategy.md").read_text(encoding="utf-8")
    assert 'id="temporary-direct-implementation"' in rule
    assert 'id="execution-topology-and-dispatch"' in rule
    assert "READ_ONLY" in rule and "NON_INTERACTIVE" in rule
    assert "不另存內容相同的完整 Task Pack" in rule
    for number in (5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45, 49):
        assert len(list((ROOT / "specs").glob(f"{number}-*/progress.md"))) == 1
