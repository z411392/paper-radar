"""Verify this owner's persisted page/batch/observation chain in one DB snapshot."""

import hashlib
import json
import re
import sqlite3
from collections import defaultdict

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.exceptions.harvest_error import HarvestError


class PubmedHarvestReceiptValidator:
    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def _pmids(cls, value: str) -> tuple[str, ...]:
        data = json.loads(value)
        if (
            not isinstance(data, list)
            or len(data) > 10000
            or any(
                not isinstance(item, str) or re.fullmatch(r"[1-9][0-9]{0,9}", item) is None
                for item in data
            )
            or len(data) != len(set(data))
            or cls._json(data) != value
        ):
            raise HarvestError("invalid_pubmed_harvest_state")
        return tuple(data)

    @staticmethod
    def _object(row: sqlite3.Row, object_id: str, digest: str) -> None:
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or object_id != "raw:" + digest
            or row["object_hash"] != digest
            or row["object_kind"] != "raw"
            or row["object_state"] != "available"
        ):
            raise HarvestError("pubmed_raw_object_mismatch")

    @classmethod
    def _verify(cls, connection: sqlite3.Connection, plan: CompiledSourceQuery, unit: sqlite3.Row) -> None:
        limit = plan.maximum_window_results + 1
        pages = connection.execute(
            "SELECT p.*,o.content_sha256 AS object_hash,o.kind AS object_kind,o.state AS object_state "
            "FROM pubmed_harvest_pages p LEFT JOIN object_registry o ON o.object_id=p.search_object_id "
            "WHERE p.unit_id=? ORDER BY p.start_index LIMIT ?", (unit["id"], limit),
        ).fetchall()
        batches = connection.execute(
            "SELECT b.*,o.content_sha256 AS object_hash,o.kind AS object_kind,o.state AS object_state "
            "FROM pubmed_bibliography_batches b "
            "LEFT JOIN object_registry o ON o.object_id=b.payload_object_id "
            "WHERE b.unit_id=? ORDER BY b.start_index,b.batch_offset LIMIT ?", (unit["id"], limit),
        ).fetchall()
        observations = connection.execute(
            "SELECT * FROM source_observations WHERE unit_id=? LIMIT ?", (unit["id"], limit),
        ).fetchall()
        if any(len(rows) >= limit for rows in (pages, batches, observations)):
            raise HarvestError("invalid_pubmed_harvest_state")
        by_page: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for batch in batches:
            by_page[batch["start_index"]].append(batch)
        by_id = {row["id"]: row for row in observations}
        used_observations: set[str] = set()
        all_pmids: set[str] = set()
        cursor, completed, total = 0, 0, None
        pending_seen = False
        for page in pages:
            pmids = cls._pmids(page["pmids_json"])
            if (
                pending_seen
                or page["start_index"] != cursor
                or not 0 <= page["total_results"] <= plan.maximum_window_results
                or (total is not None and total != page["total_results"])
                or len(pmids) != min(plan.page_size, max(0, page["total_results"] - cursor))
                or (not pmids and (cursor != 0 or page["total_results"] != 0))
                or all_pmids.intersection(pmids)
            ):
                raise HarvestError("invalid_pubmed_harvest_state")
            total = page["total_results"]
            all_pmids.update(pmids)
            fingerprint = hashlib.sha256(cls._json({
                "query_fingerprint": plan.query_fingerprint,
                "start_index": page["start_index"], "total_results": total, "pmids": list(pmids),
            }).encode("utf-8")).hexdigest()
            if fingerprint != page["page_fingerprint"]:
                raise HarvestError("invalid_pubmed_harvest_state")
            cls._object(page, page["search_object_id"], page["search_sha256"])
            offset = 0
            for batch in by_page.pop(page["start_index"], []):
                values = cls._pmids(batch["pmids_json"])
                if (
                    batch["batch_offset"] != offset
                    or not 1 <= len(values) <= 200
                    or pmids[offset:offset + len(values)] != values
                    or batch["parser_version"] != "pubmed-eutils-parser-v1"
                ):
                    raise HarvestError("invalid_pubmed_harvest_state")
                cls._object(batch, batch["payload_object_id"], batch["response_sha256"])
                for pmid in values:
                    identity = "observation:" + hashlib.sha256(cls._json([
                        unit["id"], "pubmed", pmid, batch["payload_object_id"], batch["parser_version"],
                    ]).encode("utf-8")).hexdigest()
                    observation = by_id.get(identity)
                    if (
                        observation is None
                        or observation["source"] != "pubmed"
                        or observation["native_id"] != pmid
                        or observation["payload_object_id"] != batch["payload_object_id"]
                        or observation["parser_version"] != batch["parser_version"]
                        or observation["observed_at"] != batch["observed_at"]
                        or observation["native_updated_at"] is not None
                        or identity in used_observations
                    ):
                        raise HarvestError("invalid_pubmed_harvest_state")
                    used_observations.add(identity)
                offset += len(values)
            expected_state = "complete" if offset == len(pmids) else (
                "searched" if offset == 0 else "bibliography_partial"
            )
            if page["state"] != expected_state or page["next_batch_offset"] != offset:
                raise HarvestError("invalid_pubmed_harvest_state")
            if expected_state == "complete":
                cursor += len(pmids)
                completed += 1
            else:
                pending_seen = True
        if by_page or used_observations != set(by_id) or completed != unit["checkpoint_version"]:
            raise HarvestError("invalid_pubmed_harvest_state")
        expected_state = "pending" if completed == 0 else (
            "verified_empty" if total == 0 else "succeeded" if cursor == total else "partial"
        )
        if unit["state"] != expected_state:
            raise HarvestError("invalid_pubmed_harvest_state")
        if completed:
            expected_cursor = cls._json({"format_version": 1, "next_start": cursor, "total_results": total})
            expected_coverage = cls._json({
                "format_version": 1, "record_count": cursor,
                "complete": expected_state in {"succeeded", "verified_empty"},
            })
            if unit["cursor_json"] != expected_cursor or unit["coverage_json"] != expected_coverage:
                raise HarvestError("invalid_pubmed_harvest_state")
        elif unit["cursor_json"] is not None or unit["coverage_json"] != "{}":
            raise HarvestError("invalid_pubmed_harvest_state")

    @classmethod
    def verify(cls, connection: sqlite3.Connection, plan: CompiledSourceQuery, unit: sqlite3.Row) -> None:
        try:
            cls._verify(connection, plan, unit)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError) as exc:
            raise HarvestError("invalid_pubmed_harvest_state") from exc
