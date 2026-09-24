import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone

from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_window import PreparedPubmedBibliographyObservation
from libs.discovery.exceptions.pubmed_window_error import PubmedWindowError


class PreparePubmedBibliographyObservations:
    @staticmethod
    def _time(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PubmedWindowError("invalid_pubmed_observed_at")
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise PubmedWindowError("invalid_pubmed_observed_at") from None

    @staticmethod
    def _json(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise PubmedWindowError("invalid_pubmed_bibliography") from exc

    def __call__(
        self,
        *,
        business_key: str,
        start_index: int,
        batch: PubmedBibliographyBatch,
        raw_object_id: str,
        observed_at: datetime,
    ) -> tuple[PreparedPubmedBibliographyObservation, ...]:
        if not isinstance(business_key, str) or not business_key:
            raise PubmedWindowError("invalid_pubmed_business_key")
        if type(start_index) is not int or start_index < 0:
            raise PubmedWindowError("invalid_pubmed_start")
        if not isinstance(batch, PubmedBibliographyBatch) or not batch.records:
            raise PubmedWindowError("invalid_pubmed_bibliography")
        if raw_object_id != "raw:" + batch.response_sha256:
            raise PubmedWindowError("pubmed_raw_object_mismatch")
        observed = self._time(observed_at)
        result = []
        seen = set()
        for record in batch.records:
            if record.pmid in seen:
                raise PubmedWindowError("duplicate_pubmed_bibliography")
            seen.add(record.pmid)
            record_json = self._json(asdict(record))
            content_fingerprint = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
            identity = self._json(
                {
                    "business_key": business_key,
                    "pmid": record.pmid,
                    "content_fingerprint": content_fingerprint,
                    "raw_object_id": raw_object_id,
                    "parser_version": batch.parser_version,
                }
            )
            observation_id = "pubmed-observation:" + hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()
            result.append(
                PreparedPubmedBibliographyObservation(
                    observation_id,
                    business_key,
                    start_index,
                    record.pmid,
                    raw_object_id,
                    batch.request_fingerprint,
                    batch.response_sha256,
                    batch.parser_version,
                    record_json,
                    content_fingerprint,
                    observed,
                )
            )
        return tuple(sorted(result, key=lambda item: item.pmid))
