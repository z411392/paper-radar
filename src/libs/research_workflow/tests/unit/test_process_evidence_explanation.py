from datetime import datetime, timezone
from types import SimpleNamespace

from libs.paper_explanations.dtos.explanation_persistence import PersistedExplanation
from libs.research_workflow.application.commands.process_evidence_explanation import (
    ProcessEvidenceExplanation,
)
from libs.research_workflow.dtos.evidence_explanation import EvidenceExplanationRequest
from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.dtos.relevance_persistence import PersistedRelevanceAssessment


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)
SNAPSHOT = "snapshot:" + "1" * 64
REVISION = "revision:" + "2" * 64
WORK = "work:" + "3" * 64


class Clock:
    def now(self):
        return NOW


class Claims:
    def __call__(self, snapshot_id):
        assert snapshot_id == SNAPSHOT
        request = SimpleNamespace(
            snapshot_id=SNAPSHOT,
            revision_id=REVISION,
            work_id=WORK,
        )
        claims = SimpleNamespace(request=request)
        return SimpleNamespace(
            claims=claims,
            run_id="run:" + "a" * 64,
            generation_fingerprint="b" * 64,
        )


class Relevance:
    def __init__(self, decision="direct", state="succeeded"):
        self.decision = decision
        self.state = state

    def __call__(self, profile_id, domain_id, claims):
        del claims
        return SimpleNamespace(
            assessment=RelevanceAssessment(
                "c" * 64,
                profile_id,
                3,
                domain_id,
                1,
                SNAPSHOT,
                self.state,
                self.decision if self.state == "succeeded" else None,
                "reason" if self.state == "succeeded" else None,
                ("anchor:test",) if self.decision in {"direct", "adjacent"} else (),
                None if self.state == "succeeded" else "model_unavailable",
            )
        )


class PersistRelevance:
    def __call__(self, assessment, *, assessed_at):
        assert assessed_at == NOW
        return PersistedRelevanceAssessment(
            "relevance:test",
            assessment.execution_state,
            assessment.decision,
            False,
        )


class Reading:
    def __init__(self):
        self.calls = 0

    def __call__(self, claims):
        del claims
        self.calls += 1
        return SimpleNamespace(
            draft=SimpleNamespace(),
            run_id="run:" + "d" * 64,
            generation_fingerprint="e" * 64,
        )


class Verification:
    def __init__(self, qa_state="passed", support_state="succeeded"):
        self.qa_state = qa_state
        self.support_state = support_state

    def __call__(self, draft, claims):
        del draft, claims
        return SimpleNamespace(
            verification=SimpleNamespace(
                qa_state=self.qa_state,
                support_execution_state=self.support_state,
            ),
            support_run_id="run:" + "f" * 64,
            support_generation_fingerprint="0" * 64,
        )


class PersistExplanation:
    def __init__(self, qa_state="passed"):
        self.qa_state = qa_state
        self.calls = 0
        self.requests = []

    def __call__(self, request):
        self.calls += 1
        self.requests.append(request)
        return PersistedExplanation(
            "summary:" + "4" * 64,
            SNAPSHOT,
            REVISION,
            WORK,
            request.reading_generation_fingerprint,
            request.reading_generation_run_id,
            "model_output:" + "5" * 64,
            self.qa_state,
            "zh-TW",
            "plain-zh-TW-v1",
            True,
        )


class PublishCurrent:
    def __init__(self):
        self.calls = 0

    def __call__(self, persisted):
        self.calls += 1
        return SimpleNamespace(summary_id=persisted.summary_id)


def request():
    return EvidenceExplanationRequest(
        SNAPSHOT,
        REVISION,
        WORK,
        "personal",
        3,
        "statistics",
        1,
    )


def pipeline(*, decision="direct", qa_state="passed", support_state="succeeded"):
    reading = Reading()
    persist = PersistExplanation(qa_state)
    current = PublishCurrent()
    command = ProcessEvidenceExplanation(
        Claims(),
        Relevance(decision),
        PersistRelevance(),
        reading,
        Verification(qa_state, support_state),
        persist,
        current,
        Clock(),
    )
    return command, reading, persist, current


def test_direct_relevance_publishes_passed_current_summary():
    command, reading, persist, current = pipeline()

    result = command(request())

    assert result.state == "succeeded"
    assert result.summary_id == "summary:" + "4" * 64
    assert result.relevance_assessment_id == "relevance:test"
    assert reading.calls == persist.calls == current.calls == 1


def test_irrelevant_and_uncertain_stop_before_reading_generation():
    for decision in ("uncertain", "irrelevant"):
        command, reading, persist, current = pipeline(decision=decision)

        result = command(request())

        assert result.state == "succeeded"
        assert result.summary_id is None
        assert result.relevance_assessment_id == "relevance:test"
        assert reading.calls == persist.calls == current.calls == 0


def test_pending_qa_is_persisted_but_never_published_current():
    command, reading, persist, current = pipeline(qa_state="pending")

    result = command(request())

    assert result.state == "awaiting_external"
    assert result.error_code == "summary_verification_pending"
    assert persist.calls == 1
    assert current.calls == 0


def test_rejected_qa_is_terminal_without_current_publish():
    command, _, persist, current = pipeline(qa_state="rejected")

    result = command(request())

    assert result.state == "cancelled"
    assert result.error_code == "summary_verification_rejected"
    assert persist.calls == 1
    assert current.calls == 0


def test_frozen_profile_revision_mismatch_cancels_job():
    class ChangedRelevance:
        def __call__(self, profile_id, domain_id, claims):
            del claims
            return SimpleNamespace(
                assessment=RelevanceAssessment(
                    "c" * 64,
                    profile_id,
                    4,
                    domain_id,
                    1,
                    SNAPSHOT,
                    "succeeded",
                    "direct",
                    "reason",
                    ("anchor:test",),
                    None,
                )
            )

    command, reading, persist, current = pipeline()
    command._relevance = ChangedRelevance()

    result = command(request())

    assert result.state == "cancelled"
    assert result.error_code == "scheduled_input_stale"
    assert reading.calls == persist.calls == current.calls == 0


def test_failed_support_run_is_diagnostic_only_not_summary_foreign_key():
    command, _, persist, current = pipeline(
        qa_state="pending",
        support_state="failed",
    )

    result = command(request())

    assert result.state == "awaiting_external"
    saved = persist.requests[0]
    assert saved.support_generation_run_id is None
    assert saved.support_generation_fingerprint is None
    assert current.calls == 0


def test_successful_support_run_is_bound_to_summary_persistence():
    command, _, persist, current = pipeline(
        qa_state="passed",
        support_state="succeeded",
    )

    result = command(request())

    assert result.state == "succeeded"
    saved = persist.requests[0]
    assert saved.support_generation_run_id == "run:" + "f" * 64
    assert saved.support_generation_fingerprint == "0" * 64
    assert current.calls == 1
