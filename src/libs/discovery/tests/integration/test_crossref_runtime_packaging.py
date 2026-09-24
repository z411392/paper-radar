"""Task #98 installed-resource contract; not a replacement for the wheel gate."""

import tomllib
from pathlib import Path

import pytest

from libs.kernel.adapters.driven import bundled_workspace_migrations as bundled

ROOT = Path(__file__).resolve().parents[5]
ADDITIONS = (
    "0014-crossref-capture-claims.sql",
    "0015-crossref-capture-inbox.sql",
)


@pytest.mark.parametrize("name", ADDITIONS)
def test_claim_and_inbox_are_in_every_distribution_inventory(name: str) -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = configuration["tool"]["hatch"]["build"]["targets"]
    source = f"migrations/{name}"
    assert targets["wheel"]["force-include"].get(source) == f"libs/kernel/resources/{source}"
    assert f"/{source}" in targets["sdist"]["include"]
    assert {"file": source} in configuration["tool"]["uv"]["cache-keys"]


def test_runtime_selector_requests_contiguous_v15_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    requested = []

    class Resource:
        def joinpath(self, *parts: str):
            assert parts[:2] == ("resources", "migrations")
            requested.append(parts[-1])
            return self

        def read_bytes(self):
            # This test verifies resource selection only. Real SQL is tested separately.
            return b"-- inventory fixture\n"

    monkeypatch.setattr(bundled, "files", lambda package: Resource())
    selected = bundled.load_workspace_migrations(with_runtime=True)
    assert [item.version for item in selected] == list(range(1, 16))
    assert requested[-2:] == list(ADDITIONS)
    assert len(requested) == len(set(requested))


@pytest.mark.parametrize("selectors,count", [({}, 1), ({"with_profiles": True}, 2),
                                            ({"with_discovery": True}, 4)])
def test_existing_explicit_selectors_are_not_silently_upgraded(
    monkeypatch: pytest.MonkeyPatch, selectors: dict[str, bool], count: int,
) -> None:
    class Resource:
        def joinpath(self, *parts):
            return self

        def read_bytes(self):
            return b"-- inventory fixture\n"

    monkeypatch.setattr(bundled, "files", lambda package: Resource())
    assert len(bundled.load_workspace_migrations(**selectors)) == count
