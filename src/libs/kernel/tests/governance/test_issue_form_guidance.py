"""Check our bounded form guidance, not GitHub's complete YAML/UI schema."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
EXPECTED = {
    "epic": ("整體成果", "整體驗收", "不是只數"),
    "story": ("正式規格位置", "Git", "整合驗收", "未接受提案"),
    "task": ("所屬需求（可省略）", "允許修改", "禁止修改", "受影響使用方", "反例結果"),
    "spike": ("研究問題", "嘗試上限", "停止條件", "決策去向", "證據不足"),
    "bug": ("重現步驟", "預期與實際", "反例結果", "不強制補造 Story"),
}


def guidance_errors(kind: str, text: str) -> list[str]:
    errors = []
    if f'labels: ["type:{kind}"]' not in text:
        errors.append("existing_type_label_changed")
    if re.findall(r"^    id: (\w+)$", text, re.M) != ["human", "contract"]:
        errors.append("existing_field_ids_changed")
    if any(part not in text for part in EXPECTED[kind]):
        errors.append("missing_kind_guidance")
    if "目前指派與進度只讀指定欄位" not in text:
        errors.append("missing_assignment_boundary")
    if "未接受" not in text or "comments" not in text:
        errors.append("missing_proposal_boundary")
    return errors


@pytest.mark.parametrize("kind", tuple(EXPECTED))
def test_issue_form_guidance_matches_work_kind(kind: str) -> None:
    text = (ROOT / f".github/ISSUE_TEMPLATE/{kind}.yml").read_text(encoding="utf-8")
    assert guidance_errors(kind, text) == []


@pytest.mark.parametrize("kind", tuple(EXPECTED))
def test_identical_generic_task_guidance_is_not_enough(kind: str) -> None:
    text = f'labels: ["type:{kind}"]\n    id: human\n    id: contract\nFormal Contract\n'
    assert "missing_kind_guidance" in guidance_errors(kind, text)


@pytest.mark.parametrize("kind", tuple(EXPECTED))
def test_distinct_machine_keys_are_not_silently_changed(kind: str) -> None:
    text = (ROOT / f".github/ISSUE_TEMPLATE/{kind}.yml").read_text(encoding="utf-8")
    assert "existing_field_ids_changed" in guidance_errors(kind, text.replace("id: human", "id: new"))
    assert "existing_type_label_changed" in guidance_errors(kind, text.replace("type:", "kind:"))


def test_git_specs_are_a_valid_long_term_choice() -> None:
    rule = (ROOT / ".claude/rules/80-documentation.md").read_text(encoding="utf-8")
    assert "3.0-draft" in rule
    assert "可長期沿用" in rule
    assert "不等於防止並行覆寫" in rule
    assert "ad1d4a126b4648c241b9f133cd2dbcacd8db225a240e84bd07e87096bf701059" in rule
