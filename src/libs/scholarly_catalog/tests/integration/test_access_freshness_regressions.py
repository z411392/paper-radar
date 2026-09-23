from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from libs.scholarly_catalog.adapters.driven.safe_access_evidence_policy_adapter import (
    SafeAccessEvidencePolicyAdapter,
)
from libs.scholarly_catalog.application.commands.verify_readable_location import VerifyReadableLocation
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity
from libs.scholarly_catalog.exceptions.access_assessment_error import AccessAssessmentError
from libs.scholarly_catalog.tests.contract.test_t08_versioned_evidence import AT, _claim, _probe, _tools


def test_expired_newer_restriction_must_not_revive_older_permission(tmp_path):
    _, resolved, store, verify = _tools(tmp_path)
    old = verify(_claim(resolved.manifestation_id, expires_at=None), _probe())
    new = verify(
        _claim(
            resolved.manifestation_id,
            reader="restricted",
            automated="prohibited",
            uses=(),
            checked_at=AT + timedelta(minutes=10),
            expires_at=AT + timedelta(minutes=15),
        ),
        _probe(),
    )
    assert store.read_current(resolved.manifestation_id, AT + timedelta(minutes=5)) == old
    assert store.read_current(resolved.manifestation_id, AT + timedelta(minutes=12)) == new
    with pytest.raises(AccessAssessmentError, match="access_assessment_expired"):
        store.read_current(resolved.manifestation_id, AT + timedelta(minutes=20))


@pytest.mark.parametrize("claim", [None, {}, []], ids=["none", "mapping", "list"])
def test_invalid_claim_is_classified_before_store_access(claim):
    store = Mock()
    verify = VerifyReadableLocation(Mock(), store)
    with pytest.raises(AccessAssessmentError, match="invalid_access_evidence"):
        verify(claim, _probe())
    store.read_manifestation.assert_not_called()
    store.save.assert_not_called()


@pytest.mark.parametrize(
    "field", ["content_scope", "reader_access_signal", "automated_retrieval_signal", "evidence_source_id"]
)
def test_unhashable_claim_signal_is_classified(field):
    policy = SafeAccessEvidencePolicyAdapter(frozenset({"arxiv"}))
    identity = ManifestationAccessIdentity("manifestation:test", "arxiv", "2609.00021")
    claim = replace(_claim(identity.manifestation_id), **{field: []})
    with pytest.raises(AccessAssessmentError):
        policy.evaluate(identity, claim, _probe())


@pytest.mark.parametrize("evidence", ['{"reason":"\\ud800"}', '{"nested":{"reason":"\\udfff"}}'])
def test_escaped_surrogate_in_evidence_is_classified(evidence):
    policy = SafeAccessEvidencePolicyAdapter(frozenset({"arxiv"}))
    identity = ManifestationAccessIdentity("manifestation:test", "arxiv", "2609.00021")
    with pytest.raises(AccessAssessmentError, match="invalid_access_evidence"):
        policy.evaluate(identity, replace(_claim(identity.manifestation_id), evidence_json=evidence), _probe())


@pytest.mark.parametrize("suffix", ["\n", "\t", " ", "\r"], ids=["newline", "tab", "space", "cr"])
def test_url_control_characters_are_not_silently_normalized(suffix):
    policy = SafeAccessEvidencePolicyAdapter(frozenset({"arxiv"}))
    identity = ManifestationAccessIdentity("manifestation:test", "arxiv", "2609.00021")
    url = "https://arxiv.org/pdf/2609.00021" + suffix
    probe = replace(_probe(), requested_url=url, final_url=url, network_hops=((url, ("151.101.1.69",)),))
    with pytest.raises(AccessAssessmentError, match="unsafe_access_target"):
        policy.evaluate(identity, replace(_claim(identity.manifestation_id), location_url=url), probe)


def test_integer_is_not_accepted_as_an_ip_string():
    policy = SafeAccessEvidencePolicyAdapter(frozenset({"arxiv"}))
    identity = ManifestationAccessIdentity("manifestation:test", "arxiv", "2609.00021")
    with pytest.raises(AccessAssessmentError, match="unsafe_access_target"):
        policy.evaluate(identity, _claim(identity.manifestation_id), _probe(ips=(16909060,)))


def test_encoded_space_in_url_remains_usable():
    policy = SafeAccessEvidencePolicyAdapter(frozenset({"arxiv"}))
    identity = ManifestationAccessIdentity("manifestation:test", "arxiv", "2609.00021")
    url = "https://arxiv.org/pdf/2609.00021?label=two%20words"
    probe = replace(_probe(), requested_url=url, final_url=url, network_hops=((url, ("151.101.1.69",)),))
    result = policy.evaluate(identity, replace(_claim(identity.manifestation_id), location_url=url), probe)
    assert result.reader_access == "free"
