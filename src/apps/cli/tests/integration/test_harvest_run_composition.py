import json
from datetime import datetime, timezone
from pathlib import Path

from injector import Injector

from apps.cli.module import HarvestRunCliModule, WatchProfileCliModule
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.run_harvest_slice_port import RunHarvestSlicePort
from libs.watch_profiles.ports.import_domain_seeds_port import ImportDomainSeedsPort
from libs.watch_profiles.ports.publish_watch_profile_port import PublishWatchProfilePort

ROOT = Path(__file__).resolve().parents[5]


class SyntheticTransport:
    def __init__(self) -> None:
        self.calls = []

    def get(self, request):
        self.calls.append(request)
        body = (
            "<feed xmlns='http://www.w3.org/2005/Atom' "
            "xmlns:s='http://a9.com/-/spec/opensearch/1.1/'>"
            "<s:totalResults>1</s:totalResults><s:startIndex>0</s:startIndex>"
            "<s:itemsPerPage>1</s:itemsPerPage>"
            "<entry><id>https://arxiv.org/abs/2609.00001v1</id>"
            "<title>Synthetic fixture</title><summary>Not a real paper.</summary>"
            "<author><name>Fixture</name></author><category term='stat.ML'/>"
            "<published>2026-09-22T00:00:00Z</published>"
            "<updated>2026-09-22T00:00:00Z</updated></entry></feed>"
        ).encode()
        return SourceHttpResponse(
            200,
            body,
            (("content-type", "application/atom+xml"),),
            datetime.now(timezone.utc),
        )


def profile_payload() -> str:
    value = json.loads((ROOT / "config/watch-profile.example.json").read_text(encoding="utf-8"))
    value["domains"] = [{"id": "statistics", "revision": 1}]
    return json.dumps(value, ensure_ascii=False)


def test_live_composition_uses_shared_gate_and_does_not_refetch_completed_slice(
    tmp_path: Path,
) -> None:
    from apps.cli.module import CliModule
    from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort

    workspace = tmp_path / "workspace"
    Injector([CliModule(str(workspace), with_discovery=True)], auto_bind=False).get(InitializeWorkspacePort)()

    watch = Injector([WatchProfileCliModule(str(workspace))], auto_bind=False)
    watch.get(ImportDomainSeedsPort)((ROOT / "config/domain-seeds.json").read_text(encoding="utf-8"))
    watch.get(PublishWatchProfilePort)(profile_payload(), expected_revision=None)

    transport = SyntheticTransport()
    gate = tmp_path / "shared-arxiv-rate-limit.json"
    injector = Injector(
        [HarvestRunCliModule(str(workspace), str(gate), transport=transport)],
        auto_bind=False,
    )
    request = HarvestQueryRequest(
        "personal",
        "statistics",
        datetime(2026, 9, 22, tzinfo=timezone.utc),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
        deferred_mode="defer",
        page_size=2,
    )
    query = injector.get(BuildHarvestQueryInputPort)(request)
    run = injector.get(RunHarvestSlicePort)

    first = run(query, max_pages=1)
    assert first.stop_reason == "complete"
    assert first.progress.next_start == 1
    assert first.fetch_count == first.processed_pages == 1
    assert len(transport.calls) == 1
    assert gate.is_file()

    second = run(query, max_pages=1)
    assert second.stop_reason == "complete"
    assert second.fetch_count == second.processed_pages == 0
    assert len(transport.calls) == 1
