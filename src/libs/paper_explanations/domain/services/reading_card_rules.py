import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict

from libs.paper_explanations.domain.services.claim_extraction_rules import ClaimExtractionRules
from libs.paper_explanations.domain.services.generation_json import GenerationJson
from libs.paper_explanations.dtos.claim_candidate import ClaimCandidate
from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult, PaperClaim
from libs.paper_explanations.dtos.reading_card_draft import (
    CardStatement,
    ReadingCardDraft,
    TranslationPassage,
)
from libs.paper_explanations.dtos.structured_generation_request import MODEL_NAME, StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import (
    GenerationReceipt,
    StructuredGenerationResult,
)
from libs.paper_explanations.exceptions.claim_extraction_error import ClaimExtractionError
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.exceptions.reading_card_error import ReadingCardError
from libs.paper_explanations.prompts.reading_card_prompt import (
    OUTPUT_PROFILE, PROMPT_VERSION, SCHEMA_VERSION, SYSTEM_PROMPT, response_schema,
)
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.dtos.evidence_snapshot_readback import EvidenceSnapshotReadback


class ReadingCardRules:
    @staticmethod
    def _text(value: object, code: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 8192 or '\0' in value:
            raise ReadingCardError(code)
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise ReadingCardError(code) from None
        return value

    @classmethod
    def _glossary(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, tuple) or len(value) > 64:
            raise ReadingCardError('invalid_reading_glossary')
        found: set[str] = set()
        for pair in value:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ReadingCardError('invalid_reading_glossary')
            for item in pair:
                cls._text(item, 'invalid_reading_glossary')
            if pair[0] in found:
                raise ReadingCardError('invalid_reading_glossary')
            found.add(pair[0])
        return tuple(sorted(value))

    @classmethod
    def request(
        cls, claims: ClaimExtractionResult, evidence: EvidenceSnapshotReadback,
        glossary: tuple[tuple[str, str], ...] = (),
    ) -> StructuredGenerationRequest:
        glossary = cls._glossary(glossary)
        if (
            not isinstance(evidence, EvidenceSnapshotReadback)
            or not isinstance(evidence.snapshot, EvidenceSnapshot)
        ):
            raise ReadingCardError('invalid_reading_evidence')
        if evidence.snapshot.evidence_level != 'abstract_only':
            raise ReadingCardError('reading_coverage_not_supported')
        if (
            not isinstance(claims, ClaimExtractionResult)
            or not isinstance(claims.request, ClaimExtractionRequest)
            or claims.validation_state != 'anchor_bound_candidate' or not isinstance(claims.claims, tuple)
            or any(not isinstance(item, PaperClaim) for item in claims.claims)
            or not isinstance(claims.not_reported_in_read_evidence, tuple)
        ):
            raise ReadingCardError('invalid_reading_claims')
        try:
            # Reuse the owner parser: no duplicate implementation of #18's claim identity rules.
            current = ClaimExtractionRules.request(claims.request.snapshot_id, evidence)
            if current != claims.request:
                raise ReadingCardError('reading_claim_input_mismatch')
            selection = {
                'schema_version': current.schema_version, 'snapshot_id': current.snapshot_id,
                'input_fingerprint': current.input_fingerprint,
                'claims': [
                    {'claim_type': item.claim_type, 'anchor_ids': item.anchor_ids}
                    for item in claims.claims
                ],
                'not_reported_in_read_evidence': claims.not_reported_in_read_evidence,
            }
            checked = ClaimExtractionRules.parse(current, ClaimCandidate(
                claims.run_id, MODEL_NAME, 'stop',
                GenerationJson.canonical(selection, 'invalid_reading_claims'),
            ))
            if checked != claims:
                raise ReadingCardError('invalid_reading_claims')
            payload = {
                'snapshot_id': current.snapshot_id, 'revision_id': current.revision_id,
                'work_id': current.work_id,
                'evidence_fingerprint': current.evidence_fingerprint,
                'evidence_level': current.evidence_level,
                'source_text': current.source_text, 'anchors': [asdict(a) for a in current.anchors],
                'claims': [asdict(item) for item in claims.claims],
                'not_reported_in_read_evidence': claims.not_reported_in_read_evidence,
                'glossary': glossary, 'output_profile': OUTPUT_PROFILE,
            }
            schema = response_schema()
            encoded = GenerationJson.canonical(payload, 'invalid_reading_evidence')
            identity = GenerationJson.canonical({
                'model_name': MODEL_NAME, 'prompt_version': PROMPT_VERSION, 'system_prompt': SYSTEM_PROMPT,
                'schema_version': SCHEMA_VERSION, 'schema': json.loads(schema), 'payload': payload,
            }, 'invalid_reading_evidence')
            return StructuredGenerationRequest(
                'abstract_reading_card', hashlib.sha256(identity.encode()).hexdigest(),
                SYSTEM_PROMPT, encoded, 'reading_card', schema,
            )
        except (ClaimExtractionError, ModelGatewayError):
            raise ReadingCardError('invalid_reading_claims') from None

    @staticmethod
    def _references(value: object, available: set[str]) -> tuple[str, ...]:
        if (
            not isinstance(value, list) or not 1 <= len(value) <= 8
            or any(not isinstance(item, str) or item not in available for item in value)
            or len(value) != len(set(value))
        ):
            raise ReadingCardError('invalid_reading_reference')
        return tuple(value)

    @staticmethod
    def _numbers(text: str) -> Counter:
        # Literal preservation only. Baseline/subject/unit/negation QA remains a separate gate (#20).
        return Counter(re.findall(r'(?<![A-Za-z0-9_])[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', text))

    @classmethod
    def parse(
        cls, claims: ClaimExtractionResult, request: StructuredGenerationRequest,
        completion: StructuredGenerationResult,
    ) -> ReadingCardDraft:
        if not isinstance(completion, StructuredGenerationResult) or not isinstance(
            completion.receipt, GenerationReceipt
        ):
            raise ReadingCardError('invalid_reading_response')
        receipt = completion.receipt
        if (
            receipt.input_fingerprint != request.input_fingerprint or receipt.requested_model != MODEL_NAME
            or receipt.returned_model != MODEL_NAME or receipt.finish_reason != 'stop'
        ):
            raise ReadingCardError('reading_input_mismatch')
        try:
            data = GenerationJson.object(completion.content_json, 'invalid_reading_response', limit=262144)
        except ModelGatewayError:
            raise ReadingCardError('invalid_reading_response') from None
        if set(data) != {
            'schema_version', 'snapshot_id', 'input_fingerprint', 'language',
            'faithful_translation', 'plain_language_card',
        } or data['schema_version'] != SCHEMA_VERSION or data['language'] != 'zh-TW':
            raise ReadingCardError('invalid_reading_response')
        if (
            data['snapshot_id'] != claims.request.snapshot_id
            or data['input_fingerprint'] != request.input_fingerprint
        ):
            raise ReadingCardError('reading_input_mismatch')
        raw_translation, raw_card = data['faithful_translation'], data['plain_language_card']
        if (
            not isinstance(raw_translation, list) or not 1 <= len(raw_translation) <= 64
            or not isinstance(raw_card, list) or len(raw_card) > 64
        ):
            raise ReadingCardError('invalid_reading_response')
        anchors = {anchor.anchor_id for anchor in claims.request.anchors}
        translation = []
        for passage in raw_translation:
            if not isinstance(passage, dict) or set(passage) != {'text', 'anchor_ids'}:
                raise ReadingCardError('invalid_reading_response')
            translation.append(TranslationPassage(
                cls._text(passage['text'], 'invalid_reading_response'),
                cls._references(passage['anchor_ids'], anchors),
            ))
        if cls._numbers('\n'.join(p.text for p in translation)) != cls._numbers(claims.request.source_text):
            raise ReadingCardError('translation_numbers_mismatch')
        known = {item.claim_id: item for item in claims.claims}
        statements = []
        for row in raw_card:
            if not isinstance(row, dict) or set(row) != {'claim_type', 'text', 'claim_ids'}:
                raise ReadingCardError('invalid_reading_response')
            ids = cls._references(row['claim_ids'], set(known))
            if (
                not isinstance(row['claim_type'], str)
                or any(known[cid].claim_type != row['claim_type'] for cid in ids)
            ):
                raise ReadingCardError('invalid_reading_reference')
            text = cls._text(row['text'], 'invalid_reading_response')
            quotes = '\n'.join(quote for cid in ids for quote in known[cid].source_quotes)
            if not set(cls._numbers(text)) <= set(cls._numbers(quotes)):
                raise ReadingCardError('card_numbers_unsupported')
            statements.append(CardStatement(row['claim_type'], text, ids))
        return ReadingCardDraft(
            claims.request.snapshot_id, claims.request.revision_id, claims.request.work_id,
            request.input_fingerprint, claims.request.source_text, tuple(translation), tuple(statements),
            claims.not_reported_in_read_evidence, receipt,
        )
