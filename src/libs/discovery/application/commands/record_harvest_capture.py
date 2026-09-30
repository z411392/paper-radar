from datetime import datetime

from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.ports.harvest_store_port import HarvestStorePort
from libs.kernel.ports.publish_object_port import PublishObjectPort


class RecordHarvestCapture:
    def __init__(self, store: HarvestStorePort, publish_object: PublishObjectPort) -> None:
        self._store = store
        self._publish_object = publish_object

    def __call__(self, attempt_id: str, result: SourceFetchResult, recorded_at: datetime) -> HarvestAttempt:
        attempt = self._store.read(attempt_id)
        prepared = PrepareHarvestCapture()(attempt, result, recorded_at)
        if prepared.body is not None:
            ref = self._publish_object(prepared.body, "raw", "application/octet-stream", "source-response")
            if ref.object_id != prepared.raw_object_id or ref.state != "available":
                raise HarvestError("capture_object_mismatch")
        return self._store.record(attempt_id, prepared.metadata_json, recorded_at)
