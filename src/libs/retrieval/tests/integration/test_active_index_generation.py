from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)
from libs.retrieval.dtos.active_index import ActivateIndexInput
from libs.retrieval.dtos.index_generation import IndexGenerationInput
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.tests.integration.test_index_generation_manifest import (
    _generation,
)


NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)


def _ready(tmp_path: Path, *, faiss_version: str = "1.15.1"):
    _, raw, _, space, _, generation_store, config = _generation(tmp_path)
    config = IndexGenerationInput(
        config.space_id,
        config.index_kind,
        config.builder_version,
        faiss_version,
    )
    prepared = IndexGenerationRules.prepare(
        generation_store.snapshot(space.space_id),
        config,
    )
    generation_store.start(prepared, created_at=NOW)
    manifest = IndexGenerationRules.manifest(
        prepared,
        index_sha256=faiss_version[0] * 64,
    )
    generation_store.mark_ready(
        prepared,
        manifest,
        index_sha256=faiss_version[0] * 64,
        verified_at=NOW,
    )
    return raw, space, prepared


def test_first_activation_and_reader_pin(tmp_path: Path) -> None:
    raw, space, generation = _ready(tmp_path)
    store = _active_store(raw)

    active = store.activate(
        ActivateIndexInput(
            space.space_id,
            generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )
    pin = store.pin(space.space_id)

    assert active.pointer_version == 1
    assert active.replayed is False
    assert pin == active
    assert pin.generation_id == generation.generation_id
    assert pin.relative_directory == generation.relative_directory
    assert pin.dimension == 4
    assert pin.dtype == "float32"
    assert pin.metric == "inner_product"
    assert pin.index_sha256
    assert pin.manifest_sha256


def test_exact_activation_replay_preserves_version_and_time(
    tmp_path: Path,
) -> None:
    raw, space, generation = _ready(tmp_path)
    store = _active_store(raw)
    request = ActivateIndexInput(
        space.space_id,
        generation.generation_id,
        None,
        None,
    )

    first = store.activate(request, activated_at=NOW)
    replay = store.activate(
        request,
        activated_at=NOW + timedelta(hours=1),
    )

    assert first.pointer_version == replay.pointer_version == 1
    assert replay.activated_at == NOW
    assert replay.replayed is True


def test_switch_requires_exact_current_pointer_cas(tmp_path: Path) -> None:
    raw, space, first_generation = _ready(tmp_path)
    _, _, second_generation = _ready(
        tmp_path,
        faiss_version="2.15.1",
    )
    store = _active_store(raw)
    first = store.activate(
        ActivateIndexInput(
            space.space_id,
            first_generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )

    second = store.activate(
        ActivateIndexInput(
            space.space_id,
            second_generation.generation_id,
            first.generation_id,
            first.pointer_version,
        ),
        activated_at=NOW + timedelta(minutes=1),
    )

    assert second.pointer_version == 2
    assert second.generation_id == second_generation.generation_id
    assert second.replayed is False

    with pytest.raises(ActiveIndexError, match="active_index_stale"):
        store.activate(
            ActivateIndexInput(
                space.space_id,
                first_generation.generation_id,
                first.generation_id,
                first.pointer_version,
            ),
            activated_at=NOW + timedelta(minutes=2),
        )


def test_reader_pin_remains_stable_after_later_activation(
    tmp_path: Path,
) -> None:
    raw, space, first_generation = _ready(tmp_path)
    _, _, second_generation = _ready(
        tmp_path,
        faiss_version="2.15.1",
    )
    store = _active_store(raw)
    first = store.activate(
        ActivateIndexInput(
            space.space_id,
            first_generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )
    pinned = store.pin(space.space_id)

    store.activate(
        ActivateIndexInput(
            space.space_id,
            second_generation.generation_id,
            first.generation_id,
            first.pointer_version,
        ),
        activated_at=NOW + timedelta(minutes=1),
    )

    assert pinned == first
    assert pinned.generation_id == first_generation.generation_id
    assert store.pin(space.space_id).generation_id == second_generation.generation_id


def test_building_generation_cannot_be_activated(tmp_path: Path) -> None:
    _, raw, _, space, _, generation_store, config = _generation(tmp_path)
    prepared = IndexGenerationRules.prepare(
        generation_store.snapshot(space.space_id),
        config,
    )
    generation_store.start(prepared, created_at=NOW)
    store = _active_store(raw)

    with pytest.raises(ActiveIndexError, match="active_generation_not_ready"):
        store.activate(
            ActivateIndexInput(
                space.space_id,
                prepared.generation_id,
                None,
                None,
            ),
            activated_at=NOW,
        )


def test_generation_space_must_match_activation_space(tmp_path: Path) -> None:
    raw, _, generation = _ready(tmp_path)
    store = _active_store(raw)

    with pytest.raises(ActiveIndexError, match="active_generation_space_mismatch"):
        store.activate(
            ActivateIndexInput(
                "embspace:" + "f" * 64,
                generation.generation_id,
                None,
                None,
            ),
            activated_at=NOW,
        )


def _active_store(raw):
    from libs.retrieval.adapters.driven.sqlite_active_index_store_adapter import (
        SqliteActiveIndexStoreAdapter,
    )

    return SqliteActiveIndexStoreAdapter(raw.connect)


def test_two_writers_from_same_pin_allow_only_one_switch(
    tmp_path: Path,
) -> None:
    raw, space, first_generation = _ready(tmp_path)
    _, _, second_generation = _ready(
        tmp_path,
        faiss_version="2.15.1",
    )
    _, _, third_generation = _ready(
        tmp_path,
        faiss_version="3.15.1",
    )
    store = _active_store(raw)
    first = store.activate(
        ActivateIndexInput(
            space.space_id,
            first_generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )
    barrier = Barrier(2)

    def switch(target):
        barrier.wait(timeout=5)
        try:
            return store.activate(
                ActivateIndexInput(
                    space.space_id,
                    target.generation_id,
                    first.generation_id,
                    first.pointer_version,
                ),
                activated_at=NOW + timedelta(minutes=1),
            )
        except ActiveIndexError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                switch,
                (second_generation, third_generation),
            )
        )

    winners = [item for item in results if not isinstance(item, str)]
    failures = [item for item in results if isinstance(item, str)]
    assert len(winners) == 1
    assert failures == ["active_index_stale"]
    assert store.pin(space.space_id).generation_id == winners[0].generation_id
    assert store.pin(space.space_id).pointer_version == 2
