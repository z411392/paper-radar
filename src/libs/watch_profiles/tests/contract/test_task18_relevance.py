import copy
import json
from dataclasses import asdict, replace
from unittest.mock import Mock

import pytest

from libs.paper_explanations.application.commands.extract_paper_claims import ExtractPaperClaims
from libs.paper_explanations.dtos.claim_candidate import ClaimCandidate
from libs.paper_explanations.prompts.claim_extraction_prompt import MODEL_NAME
from libs.paper_explanations.tests.fixtures.claim_evidence import canonical, evidence, response
from libs.watch_profiles.application.commands.assess_paper_relevance import AssessPaperRelevance
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.dtos.relevance_assessment import RelevanceCandidate
from libs.watch_profiles.exceptions.relevance_assessment_error import (
    RelevanceAssessmentError,
    RelevanceModelError,
)
from libs.watch_profiles.prompts.relevance_prompt import response_schema


def extracted(text=None):
    e = evidence() if text is None else evidence(text)
    usecase = ExtractPaperClaims(
        lambda _: e, lambda r: ClaimCandidate("run:test", MODEL_NAME, "stop", canonical(response(r)))
    )
    return usecase(e.snapshot.snapshot_id)


def profile(**changes):
    base = ProfileRevision("personal", 3, 3, "a" * 64, "active", "我喜歡羽球與統計；私人偏好SECRET_NOTE",
                           '{"allow_preprints":true,"exclude":[],"free_only":false,'
                           '"include":[],"languages":[],"sources":["arxiv"]}',
                           (("badminton", 2),))
    return replace(base, **changes)


def domain(**changes):
    base = DomainDefinition("badminton", 2, "羽球", ("badminton", "羽球"), ("badminton",), (), ("arxiv",), ())
    return replace(base, **changes)


def assessment_response(req):
    payload = json.loads(req.payload_json)
    return {"schema_version": req.schema_version, "snapshot_id": req.snapshot_id,
            "profile_id": req.profile_id, "domain_id": req.domain_id,
            "input_fingerprint": req.input_fingerprint,
            "profile_revision": req.profile_revision, "domain_revision": req.domain_revision,
            "decision": "direct", "recommendation_reason": "原文直接研究羽球，符合你的關注範圍。",
            "anchor_ids": [a["anchor_id"] for a in payload["evidence"]["anchors"]]}


def setup(
    claims=None, before=None, after=None, domain_value=None, mutate=None, raw=None, failure=None, **envelope
):
    claims = extracted() if claims is None else claims
    before = profile() if before is None else before
    after = before if after is None else after
    profiles = Mock(side_effect=[before, after])
    domains = Mock(return_value=domain() if domain_value is None else domain_value)
    def generate(req):
        if failure:
            raise failure
        result = assessment_response(req)
        if mutate:
            mutate(result, req)
        text = raw if raw is not None else canonical(result)
        return replace(RelevanceCandidate(MODEL_NAME, "stop", text), **envelope)
    model = Mock(side_effect=generate)
    return claims, AssessPaperRelevance(profiles, domains, model), profiles, domains, model


def test_success_separates_recommendation_from_author_facts_and_rechecks_profile():
    claims, assess, profiles, domains, model = setup()
    original = copy.deepcopy(claims)
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "succeeded" and result.decision == "direct"
    assert result.error_code is None
    assert result.profile_revision == 3 and result.domain_revision == 2
    assert claims == original
    assert "SECRET_NOTE" not in claims.request.payload_json
    assert "SECRET_NOTE" in model.call_args.args[0].payload_json
    assert result.recommendation_reason not in [q for c in claims.claims for q in c.source_quotes]
    assert profiles.call_count == 2
    domains.assert_called_once_with("badminton", 2)
    assert "score" not in asdict(result)


@pytest.mark.parametrize("decision", ["direct", "adjacent", "uncertain", "irrelevant"])
def test_decision_labels_are_not_execution_states(decision):
    claims, assess, _, _, _ = setup(mutate=lambda d, _: d.update(decision=decision))
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "succeeded" and result.decision == decision


@pytest.mark.parametrize(
    "code", ["timeout", "budget_blocked", "transport_error", "model_unavailable", "refused"]
)
def test_expected_model_failures_are_not_irrelevant(code):
    claims, assess, _, _, model = setup(failure=RelevanceModelError(code))
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed"
    assert result.decision is None and result.recommendation_reason is None
    assert result.anchor_ids == () and result.error_code == code
    assert model.call_count == 1


def test_unexpected_programming_error_is_not_swallowed_as_irrelevant():
    claims, assess, _, _, _ = setup(failure=RuntimeError("unexpected implementation error"))
    with pytest.raises(RuntimeError, match="unexpected implementation error"):
        assess("personal", "badminton", claims)


@pytest.mark.parametrize("after", [profile(revision=4, current_revision=4, fingerprint="b" * 64),
                                   profile(lifecycle="paused"),
                                   profile(scope_text="changed without revision")])
def test_late_response_cannot_be_current_after_profile_change(after):
    claims, assess, _, _, _ = setup(after=after)
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "stale" and result.decision is None
    assert result.recommendation_reason is None and result.anchor_ids == ()
    assert result.error_code == "profile_changed"


def test_profile_change_also_supersedes_a_late_model_failure():
    claims, assess, _, _, _ = setup(after=profile(lifecycle="paused"), failure=RelevanceModelError("timeout"))
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "stale" and result.decision is None


@pytest.mark.parametrize("before", [profile(lifecycle="paused"), profile(current_revision=4),
                                    profile(domains=()), profile(revision=True),
                                    profile(domains=(("badminton", True),)),
                                    profile(domains=(("badminton", 2), ("badminton", 2)))])
def test_invalid_or_inactive_profile_fails_before_model(before):
    claims, assess, _, _, model = setup(before=before)
    with pytest.raises(RelevanceAssessmentError):
        assess("personal", "badminton", claims)
    model.assert_not_called()


def test_exact_domain_revision_is_required():
    claims, assess, _, _, model = setup(domain_value=domain(revision=3))
    with pytest.raises(RelevanceAssessmentError, match="invalid_relevance_input"):
        assess("personal", "badminton", claims)
    model.assert_not_called()


def test_profile_revision_changes_only_relevance_input_identity_not_claims():
    claims = extracted()
    values = []
    for state in (profile(), profile(revision=4, current_revision=4, fingerprint="b" * 64)):
        _, assess, _, _, model = setup(claims=claims, before=state)
        assess("personal", "badminton", claims)
        values.append(model.call_args.args[0].input_fingerprint)
    assert values[0] != values[1]
    assert claims == extracted()


@pytest.mark.parametrize("raw", ["null", "[]", "{}", '{"a":1,"a":2}', '{"score":NaN}',
                                 '{"a":"\\ud800"}', "x" * 65537])
def test_bad_model_output_is_failed_not_irrelevant(raw):
    claims, assess, _, _, _ = setup(raw=raw)
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed" and result.decision is None


@pytest.mark.parametrize("key,value", [("score", 0.99), ("decision", "failed"), ("decision", []),
                                       ("profile_revision", True), ("profile_revision", 4),
                                       ("domain_revision", 3), ("snapshot_id", "another"),
                                       ("anchor_ids", []), ("anchor_ids", ["other"]),
                                       ("recommendation_reason", "")])
def test_response_schema_identity_and_citations_are_strict(key, value):
    claims, assess, _, _, _ = setup(mutate=lambda d, _: d.update({key: value}))
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed" and result.decision is None


@pytest.mark.parametrize("envelope", [{"model_name": "other-model"}, {"finish_reason": "length"}])
def test_model_substitution_and_truncation_fail(envelope):
    claims, assess, _, _, _ = setup(**envelope)
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed" and result.decision is None


@pytest.mark.parametrize(
    "text", ["We study athletes in generic sport.", "We use nonbadmintonlike exercises."]
)
def test_generic_sport_or_substring_is_not_direct_badminton(text):
    claims = extracted(text)
    _, assess, _, _, _ = setup(claims=claims)
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed" and result.error_code == "direct_evidence_missing"
    _, adjacent, _, _, _ = setup(claims=claims, mutate=lambda d, _: d.update(decision="adjacent"))
    assert adjacent("personal", "badminton", claims).decision == "adjacent"


@pytest.mark.parametrize("text", ["BADMINTON movements were studied.", "研究羽球🏸選手。", "研究羽毛球選手。"])
def test_explicit_badminton_term_is_a_necessary_surface_evidence_check(text):
    claims = extracted(text)
    _, assess, _, _, _ = setup(claims=claims)
    assert assess("personal", "badminton", claims).decision == "direct"


def test_direct_badminton_must_cite_the_relevant_passage_not_an_unrelated_anchor():
    claims, assess, _, _, _ = setup(mutate=lambda d, r: d.update(
        anchor_ids=[json.loads(r.payload_json)["evidence"]["anchors"][-1]["anchor_id"]]))
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed" and result.error_code == "direct_evidence_missing"


def test_relevance_schema_has_no_probability_or_fact_rewrite_field():
    schema = json.loads(response_schema())
    assert schema["additionalProperties"] is False
    assert not {"score", "probability", "claims", "translated_abstract"} & set(schema["properties"])


@pytest.mark.parametrize("field,value", [("input_fingerprint", "f" * 64),
                                       ("profile_id", "someone_else"), ("domain_id", "statistics")])
def test_same_revisions_do_not_allow_cross_request_response_mixup(field, value):
    claims, assess, _, _, _ = setup(mutate=lambda d, _: d.update({field: value}))
    result = assess("personal", "badminton", claims)
    assert result.execution_state == "failed"
    assert result.decision is None and result.error_code == "relevance_version_mismatch"
