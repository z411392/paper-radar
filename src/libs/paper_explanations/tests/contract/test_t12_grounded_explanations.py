from dataclasses import replace
from unittest.mock import Mock

import pytest

from libs.paper_explanations.application.commands.verify_explanation import VerifyExplanation
from libs.paper_explanations.domain.services.explanation_verification_rules import (
    ExplanationVerificationRules,
)
from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult, PaperClaim
from libs.paper_explanations.dtos.explanation_verification import (
    SupportStatementVerdict,
    SupportVerificationCandidate,
)
from libs.paper_explanations.dtos.reading_card_draft import CardStatement, ReadingCardDraft, TranslationPassage
from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt
from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor

SID = "snapshot:" + "1" * 64
RID = "revision:" + "2" * 64
WID = "work:" + "3" * 64
AID = "anchor:" + "4" * 64
CID = "claim:" + "5" * 64
QUOTE = "The proposed method reduced error to 0.42 m compared with a baseline of 0.58 m."


def fixture(
    statement: str = "新方法誤差為 0.42 m，基準為 0.58 m。",
    translation: str | None = None,
    quote: str = QUOTE,
    claim_type: str = "result",
):
    anchor = EvidenceAnchor(AID, SID, "abstract", "p1", quote, 0, len(quote), None)
    request = ClaimExtractionRequest(
        SID,
        RID,
        WID,
        "6" * 64,
        "abstract_only",
        quote,
        (anchor,),
        "google/gemini-3.8-flash",
        "p",
        "s",
        "sys",
        "{}",
        "{}",
        "7" * 64,
    )
    claim = PaperClaim(CID, claim_type, (AID,), (quote,))
    claims = ClaimExtractionResult(request, "run:x", (claim,), ())
    receipt = GenerationReceipt(
        "8" * 64,
        "9" * 64,
        "gen",
        "google/gemini-3.8-flash",
        "google/gemini-3.8-flash",
        "provider",
        1,
        1,
        "0.1",
        "stop",
    )
    translation = statement if translation is None else translation
    draft = ReadingCardDraft(
        SID,
        RID,
        WID,
        "8" * 64,
        quote,
        (TranslationPassage(translation, (AID,)),),
        (CardStatement(claim_type, statement, (CID,)),),
        (),
        receipt,
    )
    return draft, claims


def test_deterministic_accepts_role_unit_number_preserving_card():
    draft, claims = fixture()
    report = ExplanationVerificationRules.deterministic(draft, claims)
    assert report.verdict == "passed" and report.findings == ()


def test_same_language_swapped_proposed_and_baseline_is_rejected():
    quote = "新方法誤差為 0.42 m，基準為 0.58 m。"
    draft, claims = fixture(
        "新方法誤差為 0.58 m，基準為 0.42 m。",
        quote=quote,
    )
    report = ExplanationVerificationRules.deterministic(draft, claims)
    assert report.verdict == "rejected"
    assert "role_value_mismatch" in {finding.code for finding in report.findings}


def test_unit_change_is_rejected():
    draft, claims = fixture("新方法誤差為 0.42 cm，基準為 0.58 m。")
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "unit_mismatch" in codes


def test_added_causality_is_rejected():
    draft, claims = fixture("新方法導致誤差為 0.42 m，基準為 0.58 m。")
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "causal_unsupported" in codes


def test_dropped_negation_is_rejected():
    quote = "The method did not improve accuracy."
    draft, claims = fixture("這個方法提高準確率。", quote=quote)
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "negation_dropped" in codes


def test_translation_number_multiplicity_is_checked():
    draft, claims = fixture(translation="新方法誤差為 0.42 m，0.42 m，基準為 0.58 m。")
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "number_mismatch" in codes


def test_empty_card_is_not_publishable():
    draft, claims = fixture()
    draft = replace(draft, plain_language_card=())
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "empty_reading_card" in codes


def supported_candidate(request, verdict: str = "supported"):
    return SupportVerificationCandidate(
        request.snapshot_id,
        request.input_fingerprint,
        tuple(
            SupportStatementVerdict(statement.statement_index, verdict, statement.claim_ids)
            for statement in request.statements
        ),
    )


def test_support_verifier_can_only_judge_existing_statement_and_claim_ids():
    draft, claims = fixture()
    report = ExplanationVerificationRules.deterministic(draft, claims)
    request = ExplanationVerificationRules.support_request(draft, claims, report)
    result = ExplanationVerificationRules.parse_support(request, supported_candidate(request))
    assert result.statements[0].verdict == "supported"
    item = supported_candidate(request).statements[0]
    bad = replace(
        supported_candidate(request),
        statements=(replace(item, claim_ids=("claim:" + "f" * 64,)),),
    )
    with pytest.raises(ExplanationVerificationError, match="support_response_mismatch"):
        ExplanationVerificationRules.parse_support(request, bad)


def test_unsupported_statement_rejects_qa_and_failure_stays_pending():
    draft, claims = fixture()
    verifier = Mock(side_effect=lambda request: supported_candidate(request, "unsupported"))
    result = VerifyExplanation(verifier)(draft, claims)
    assert result.qa_state == "rejected" and result.support_execution_state == "succeeded"

    verifier = Mock(side_effect=ModelGatewayError("timeout"))
    result = VerifyExplanation(verifier)(draft, claims)
    assert result.qa_state == "pending" and result.support_execution_state == "failed"


def test_uncertain_statement_never_becomes_passed():
    draft, claims = fixture()
    verifier = Mock(side_effect=lambda request: supported_candidate(request, "uncertain"))
    assert VerifyExplanation(verifier)(draft, claims).qa_state == "pending"



def test_non_negating_chinese_words_do_not_trigger_hard_negation_failure():
    quote = "We compare different methods and discuss future work."
    text = "我們比較不同方法，並討論未來工作。"
    draft, claims = fixture(text, translation=text, quote=quote)
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "negation_added" not in codes
    assert "negation_dropped" not in codes


def test_explicit_chinese_negation_matches_source_negation():
    quote = "The method did not improve accuracy."
    text = "這個方法沒有提高準確率。"
    draft, claims = fixture(text, translation=text, quote=quote)
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "negation_added" not in codes
    assert "negation_dropped" not in codes


def test_unit_prefix_cannot_turn_milliseconds_into_meters():
    quote = "Latency was 0.42 ms."
    text = "延遲為 0.42 m。"
    draft, claims = fixture(text, translation=text, quote=quote)
    codes = {finding.code for finding in ExplanationVerificationRules.deterministic(draft, claims).findings}
    assert "unit_mismatch" in codes


def test_cross_language_hour_unit_is_normalized() -> None:
    quote = "We pretrain on about 4,000 hours of fMRI."
    text = "我們使用約 4,000 小時的 fMRI 資料進行預訓練。"
    draft, claims = fixture(text, translation=text, quote=quote)
    codes = {
        finding.code
        for finding in ExplanationVerificationRules.deterministic(draft, claims).findings
    }
    assert "unit_mismatch" not in codes


def test_cross_language_baseline_word_order_does_not_fake_role_swap() -> None:
    quote = "Across 5 datasets, 11 parcellations and 6 targets, we match the KRR baseline."
    text = "相較於 KRR 基準，我們在 5 個資料集、11 種分區與 6 個目標上達到相當表現。"
    draft, claims = fixture(text, translation=text, quote=quote)
    codes = {
        finding.code
        for finding in ExplanationVerificationRules.deterministic(draft, claims).findings
    }
    assert "role_value_mismatch" not in codes
