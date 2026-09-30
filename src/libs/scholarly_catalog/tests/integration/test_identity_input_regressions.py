from dataclasses import replace
from unittest.mock import Mock

import pytest

from libs.scholarly_catalog.adapters.driven.sqlite_paper_identity_store_adapter import (
    SqlitePaperIdentityStoreAdapter,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.tests.contract.test_t07_versioned_evidence import (
    AT,
    _identity_tools,
    _observation,
)


@pytest.mark.parametrize("value", [None, {}, []], ids=["none", "mapping", "list"])
def test_invalid_observation_is_classified_without_database_changes(tmp_path, value):
    root, resolve, _ = _identity_tools(tmp_path)
    before = (root / "state/app.sqlite3").read_bytes()
    with pytest.raises(PaperIdentityError, match="invalid_observation"):
        resolve(value)
    assert (root / "state/app.sqlite3").read_bytes() == before


@pytest.mark.parametrize("field", ["manifestation_kind", "publication_status"])
def test_unhashable_observation_enum_is_classified(tmp_path, field):
    _, resolve, _ = _identity_tools(tmp_path)
    value = replace(_observation("obs:invalid", "2609.00091v1", "1" * 64), **{field: []})
    with pytest.raises(PaperIdentityError, match="invalid_observation"):
        resolve(value)


@pytest.mark.parametrize(
    "evidence",
    [
        '{"reason":NaN}',
        '{"reason":Infinity}',
        '{"reason":-Infinity}',
        '{"reason":1e999}',
        '{"reason":"\\ud800"}',
        '{"nested":{"reason":"\\udfff"}}',
    ],
    ids=["nan", "infinity", "negative-infinity", "overflow", "surrogate", "nested-surrogate"],
)
def test_invalid_decision_evidence_fails_before_opening_database(evidence):
    connect = Mock(side_effect=AssertionError("invalid evidence must not open a database"))
    store = SqlitePaperIdentityStoreAdapter(connect)
    with pytest.raises(PaperIdentityError, match="invalid_identity_evidence"):
        store.merge_alias("work:alias", "work:target", evidence, AT)
    connect.assert_not_called()
