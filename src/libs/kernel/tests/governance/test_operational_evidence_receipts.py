import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]


def test_operational_evidence_story_keeps_receipt_classes_separate() -> None:
    story = (
        ROOT / "specs/49-operational-evidence/spec.md"
    ).read_text(encoding="utf-8")
    requirements = (
        ROOT / "docs/delivery/requirements-specification.md"
    ).read_text(encoding="utf-8")
    progress = (
        ROOT / "specs/49-operational-evidence/progress.md"
    ).read_text(encoding="utf-8")

    assert "獨立 Reviewer 讀 exact SHA" in story
    assert (
        "未執行 live、尚未審查或只有 schema 測試時，產品 Exit 不得寫 PROVEN"
        in story
    )
    assert (
        "deterministic PASS、live capability、data-backed consumer、"
        "independent ACCEPT 分開"
        in requirements
    )
    assert "真來源／真模型／真實寄送／獨立 ACCEPT 收據皆未提供" in progress


def test_repository_manifest_cannot_turn_author_oracle_into_acceptance() -> None:
    manifest = json.loads(
        (ROOT / ".authority/manifests/git-baseline.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["issue_authority_enabled"] is False
    assert manifest["acceptance_receipts"] == []
