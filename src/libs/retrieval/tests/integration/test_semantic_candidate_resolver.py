from dataclasses import replace
from pathlib import Path

import pytest

from libs.retrieval.adapters.driven.sqlite_semantic_candidate_resolver_adapter import (
    SqliteSemanticCandidateResolverAdapter,
)
from libs.retrieval.dtos.search_hybrid import SearchSemanticHit
from libs.retrieval.exceptions.search_query_error import SearchQueryError
from libs.retrieval.tests.integration.test_active_index_generation import (
    NOW,
    _active_store,
    _ready,
)


def _pin(tmp_path: Path):
    raw, space, generation = _ready(tmp_path)
    store = _active_store(raw)
    from libs.retrieval.dtos.active_index import ActivateIndexInput

    pin = store.activate(
        ActivateIndexInput(
            space.space_id,
            generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )
    return raw, generation, pin


def test_semantic_ids_resolve_through_pinned_generation_to_current_documents(
    tmp_path: Path,
) -> None:
    raw, generation, pin = _pin(tmp_path)
    resolver = SqliteSemanticCandidateResolverAdapter(raw.connect)
    hits = tuple(
        SearchSemanticHit(member.embedding_id, 1.0 - index * 0.1)
        for index, member in enumerate(generation.members)
    )

    resolved = resolver.resolve(pin, hits)

    assert [hit.embedding_id for hit in resolved] == [
        member.embedding_id for member in generation.members
    ]
    assert all(hit.document_id for hit in resolved)


def test_stale_document_is_filtered_after_semantic_lookup(
    tmp_path: Path,
) -> None:
    raw, generation, pin = _pin(tmp_path)
    stale = generation.members[0]
    connection = raw.connect()
    try:
        connection.execute(
            "UPDATE search_documents SET is_current=0 WHERE id=?",
            (stale.document_id,),
        )
        connection.commit()
    finally:
        connection.close()

    resolved = SqliteSemanticCandidateResolverAdapter(raw.connect).resolve(
        pin,
        tuple(
            SearchSemanticHit(member.embedding_id, 0.9)
            for member in generation.members
        ),
    )

    assert stale.embedding_id not in {
        hit.embedding_id for hit in resolved
    }


def test_embedding_id_outside_pinned_generation_is_corruption(
    tmp_path: Path,
) -> None:
    raw, _, pin = _pin(tmp_path)

    with pytest.raises(
        SearchQueryError,
        match="semantic_index_mapping_mismatch",
    ):
        SqliteSemanticCandidateResolverAdapter(raw.connect).resolve(
            pin,
            (SearchSemanticHit(9_999_999, 0.5),),
        )


def test_pin_generation_metadata_is_revalidated(tmp_path: Path) -> None:
    raw, generation, pin = _pin(tmp_path)
    damaged = replace(pin, membership_digest="f" * 64)

    with pytest.raises(
        SearchQueryError,
        match="semantic_generation_changed",
    ):
        SqliteSemanticCandidateResolverAdapter(raw.connect).resolve(
            damaged,
            (SearchSemanticHit(generation.members[0].embedding_id, 0.5),),
        )
