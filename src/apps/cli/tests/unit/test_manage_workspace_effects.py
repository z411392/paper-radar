import json

from apps.cli.adapters.driving import manage_workspace_effects
from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.ports.read_workspace_info_port import ReadWorkspaceInfoPort
from libs.kernel.ports.set_workspace_external_effects_port import (
    SetWorkspaceExternalEffectsPort,
)


def test_effects_cli_reads_authority_then_sends_exact_cas(
    monkeypatch,
    capsys,
) -> None:
    before = WorkspaceInfo(
        workspace_id="workspace-1",
        epoch=7,
        external_effects_enabled=False,
        schema_version=17,
    )
    after = WorkspaceInfo(
        workspace_id="workspace-1",
        epoch=7,
        external_effects_enabled=True,
        schema_version=17,
    )
    calls: list[tuple[object, ...]] = []

    class FakeInjector:
        def __init__(self, *args, **kwargs) -> None:
            calls.append(("injector",))

        def get(self, interface):
            if interface is ReadWorkspaceInfoPort:
                def read() -> WorkspaceInfo:
                    calls.append(("read",))
                    return before

                return read
            if interface is SetWorkspaceExternalEffectsPort:
                def set_effects(
                    enabled: bool,
                    **expectations,
                ) -> WorkspaceInfo:
                    calls.append(("set", enabled, expectations))
                    return after

                return set_effects
            raise AssertionError(f"unexpected interface: {interface!r}")

    monkeypatch.setattr(manage_workspace_effects, "Injector", FakeInjector)

    manage_workspace_effects.run_workspace_effects_cli(
        ["effects", "enable", "--workspace", "/tmp/workspace"]
    )

    assert calls == [
        ("injector",),
        ("read",),
        (
            "set",
            True,
            {
                "expected_workspace_id": "workspace-1",
                "expected_epoch": 7,
                "expected_enabled": False,
            },
        ),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "epoch": 7,
        "external_effects_enabled": True,
        "schema_version": 17,
        "workspace_id": "workspace-1",
    }
