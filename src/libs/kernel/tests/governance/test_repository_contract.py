import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("AGENTS.md", "CLAUDE.md"),
        ("GEMINI.md", "CLAUDE.md"),
        (".agents/rules", "../.claude/rules"),
    ],
)
def test_tool_entrypoints_share_one_owner(path: str, target: str) -> None:
    link = ROOT / path
    assert link.is_symlink()
    assert link.readlink() == Path(target)
    assert link.resolve().exists()
    assert link.resolve().is_relative_to(ROOT)


def test_roster_references_canonical_rule_without_becoming_authority() -> None:
    roster = json.loads((ROOT / ".agents/roster.json").read_text(encoding="utf-8"))
    assert roster["canonical"] is False
    path, anchor = roster["authority"].split("#", 1)
    assert f'id="{anchor}"' in (ROOT / path).read_text(encoding="utf-8")
    for entry in roster["contexts"].values():
        assert entry["architect_ref"] == "shared_roles.architect"
        assert entry["reviewer_ref"] == "shared_roles.reviewer"


def test_relative_markdown_links_resolve_inside_repository() -> None:
    errors = []
    roots = [ROOT / "docs", ROOT / "specs", ROOT / ".claude/rules"]
    files = [ROOT / "README.md", ROOT / "CLAUDE.md"]
    files.extend(path for root in roots for path in root.rglob("*.md"))
    for path in files:
        text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
        for raw in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)\)", text):
            url = urlsplit(raw)
            if url.scheme or url.netloc or raw.startswith("#"):
                continue
            target = (path.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(ROOT) or not target.exists():
                errors.append(f"{path.relative_to(ROOT)}: {raw}")
    assert errors == []
