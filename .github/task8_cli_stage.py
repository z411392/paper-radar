"""Bounded author-candidate assembly; no ref, PR, Issue or Project mutations."""
import ast
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
import zlib
from pathlib import Path

REPO = "z411392/paper-radar"
INIT = "85a7d2704c0958a814e9dea3f15e7f02fb21a5a6"
PROFILES = "a01b87e26491d453105b840eb728e71afd9d701a"
CHUNKS = ["98389f65d04ca9593ca14afd734111c9204fe9da", "e14390a6c21caed3962147bf69acb107afb27ba7", "493528c1e9da5398517980aa92b35e3db9955e9f", "4e4fb0b150d1346d276b073a60570e85accecbb3"]
PAYLOAD_HASH = "0107108404fe43132df9fd25697d9339bd56f31a315c641a1d99de5b60c04c9d"
DOCS = ("specs/5-local-workspace/plan.md", "specs/5-local-workspace/progress.md")
TEMP = Path(os.environ["RUNNER_TEMP"])
STATE = TEMP / "paper-radar-cli-assembly.json"
STAGING = (".github/task8_cli_stage.py", ".github/workflows/task8-cli-stage.yml")
EXPECTED = {
    "src/apps/cli/entrypoints.py": "cadc0c5addd61d45d0c0a9cf701290e5594008dc",
    "src/apps/cli/module.py": "ab2f52ea53a3a12f705a7f219ab8203a5693d469",
    "src/apps/cli/adapters/driving/initialize_workspace.py": "1524545d336c4967b8324a373ae8ff16c9a02763",
    "src/libs/kernel/adapters/driven/bundled_workspace_migrations.py": "3fab115072fa6b9cc04cfa48fda372cbbe07affb",
    "pyproject.toml": "6f948154129acc5cb44fb427bcc100c2bcc8c3b3",
}


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def api(suffix, data=None):
    assert os.environ["GITHUB_REPOSITORY"] == REPO
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/{suffix}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def load_payload():
    parts = []
    for sha in CHUNKS:
        blob = api("git/blobs/" + sha)
        raw = base64.b64decode(blob["content"])
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == sha
        parts.append(raw)
    decoded = zlib.decompress(base64.b64decode(b"".join(parts)))
    assert len(decoded) == 37207 and hashlib.sha256(decoded).hexdigest() == PAYLOAD_HASH
    payload = json.loads(decoded)
    assert len(payload["files"]) == 13
    for name in [*payload["files"], *payload["patches"]]:
        assert ".." not in Path(name).parts and not name.startswith("/")
        assert name.startswith(("src/apps/cli/", "src/libs/kernel/", "config/")) or name == "pyproject.toml"
    return payload


def write(name, content):
    target = Path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def prepare():
    stage_head = git("rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", INIT, stage_head], check=True)
    subprocess.run(["git", "config", "user.name", "paper-radar candidate assembly"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    common = git("merge-base", stage_head, PROFILES)
    merged = subprocess.run(["git", "merge", "--no-commit", "--no-ff", PROFILES], text=True, capture_output=True)
    print(merged.stdout)
    conflicts = git("diff", "--name-only", "--diff-filter=U").splitlines()
    assert set(conflicts) <= set(DOCS), conflicts
    for name in conflicts:
        base = subprocess.check_output(["git", "show", f"{common}:{name}"]).decode()
        ours = subprocess.check_output(["git", "show", f"{stage_head}:{name}"]).decode()
        theirs = subprocess.check_output(["git", "show", f"{PROFILES}:{name}"]).decode()
        assert ours.startswith(base) and theirs.startswith(base), name
        write(name, ours + theirs[len(base):])
        subprocess.run(["git", "add", "--", name], check=True)
    assert not git("diff", "--name-only", "--diff-filter=U")
    if merged.returncode != 0:
        assert conflicts, merged.stderr
    merged_tree = git("write-tree")
    payload = load_payload()
    for name, sha in EXPECTED.items():
        assert git("hash-object", "--", name) == sha, ("upstream changed", name)
    for name in payload["files"]:
        if name not in EXPECTED:
            assert not Path(name).exists(), ("unexpected preexisting file", name)
    tests = {p: c for p, c in payload["files"].items() if "/tests/" in p}
    for name, content in tests.items():
        write(name, content)
    # Establish real CLI behavior RED on the merged, unchanged upstream source.
    command = ["uv", "run", "--locked", "--offline", "python", "-m", "pytest",
               "src/apps/cli/tests/acceptance/test_watch_profile_cli.py", "-q", "--tb=short"]
    red = subprocess.run(command, text=True, capture_output=True)
    (TEMP / "paper-radar-cli-red.log").write_text(red.stdout + red.stderr)
    print(red.stdout)
    print(red.stderr)
    assert red.returncode == 1, red.returncode
    print("CLI_RED_CONFIRMED=1")
    subprocess.run(["git", "add", "--", *tests], check=True)
    tests_tree = git("write-tree")
    for name, content in payload["files"].items():
        if name not in tests:
            write(name, content)
    for name, replacements in payload["patches"].items():
        source = Path(name).read_text(encoding="utf-8")
        for old, new in replacements:
            assert source.count(old) == 1, (name, old)
            source = source.replace(old, new)
        write(name, source)
    changed_python = [p for p in [*payload["files"], *payload["patches"]] if p.endswith(".py")]
    subprocess.run(["uv", "run", "--locked", "--offline", "ruff", "check", "--select", "I", "--fix", *changed_python], check=True)
    before = {p: ast.dump(ast.parse(Path(p).read_text())) for p in changed_python}
    subprocess.run(["uv", "run", "--locked", "--offline", "ruff", "format", *changed_python], check=True)
    assert all(ast.dump(ast.parse(Path(p).read_text())) == before[p] for p in changed_python)
    # Build metadata changed; refresh the editable resource installation, not the dependency lock.
    subprocess.run(["uv", "sync", "--locked", "--offline"], check=True)
    state = {"head": stage_head, "merge_tree": merged_tree, "tests_tree": tests_tree,
             "files": list(payload["files"]), "patches": list(payload["patches"])}
    STATE.write_text(json.dumps(state))
    print("PAYLOAD_SHA256=" + PAYLOAD_HASH)
    print("Prepared exact author payload; old tests, SQL, rules, and dependencies unchanged.")


def publish_tree(local_tree, base_tree):
    changes = git("diff", "--name-status", base_tree, local_tree).splitlines()
    entries = []
    for change in changes:
        status, name = change.split("\t", 1)
        assert status in {"A", "M", "D"}, change
        if status == "D":
            entries.append({"path": name, "mode": "100644", "type": "blob", "sha": None})
            continue
        metadata = git("ls-tree", local_tree, "--", name).split()
        mode, kind, expected = metadata[:3]
        assert kind == "blob" and mode == "100644", (name, mode, kind)
        content = subprocess.check_output(["git", "show", f"{local_tree}:{name}"])
        blob = api("git/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})
        assert blob["sha"] == expected
        entries.append({"path": name, "mode": mode, "type": kind, "sha": blob["sha"]})
    tree = api("git/trees", {"base_tree": base_tree, "tree": entries})["sha"]
    assert tree == local_tree, (tree, local_tree)
    return tree


def export():
    state = json.loads(STATE.read_text())
    assert git("rev-parse", "HEAD") == state["head"]
    source_paths = state["files"] + state["patches"]
    subprocess.run(["git", "add", "--", *source_paths], check=True)
    for name in STAGING:
        subprocess.run(["git", "rm", "--", name], check=True)
    final_tree = git("write-tree")
    stage_tree = git("show", "-s", "--format=%T", state["head"])
    merge_tree = publish_tree(state["merge_tree"], stage_tree)
    merge_commit = api("git/commits", {
        "message": "task-8 S4: integrate exact initialization and profile candidates without replacing their histories\n\nRefs #6, #7, #8, #5. Preserve both append-only Story records. No main integration or acceptance.",
        "tree": merge_tree, "parents": [state["head"], PROFILES],
    })["sha"]
    tests_tree = publish_tree(state["tests_tree"], merge_tree)
    test_commit = api("git/commits", {
        "message": "task-8 S4: define CLI upgrade, input rejection and installed-wheel oracles\n\nRefs #8. Author-written CLI tests failed against the unchanged merged source before implementation. Other new tests are not claimed as independently frozen or red-tested.",
        "tree": tests_tree, "parents": [merge_commit],
    })["sha"]
    tree = publish_tree(final_tree, tests_tree)
    commit = api("git/commits", {
        "message": "task-8 S4: wire profile commands and explicit schema upgrades with verified package resources\n\nRefs #8, #6, #5. Locked ci-fast and clean-wheel tests passed before object export. Preserve original SQL, dependency lock, business rules and existing tests. Remove temporary assembly files; no ref, Issue or Project updates, no independent ACCEPT.",
        "tree": tree, "parents": [test_commit],
    })["sha"]
    print("MERGE_COMMIT=" + merge_commit)
    print("CLI_TEST_COMMIT=" + test_commit)
    print("CLI_FINAL_TREE=" + tree)
    print("CLI_FINAL_COMMIT=" + commit)
    print("No refs, PRs, Issues or Project fields mutated by this job.")


if __name__ == "__main__":
    {"prepare": prepare, "export": export}[sys.argv[1]]()
