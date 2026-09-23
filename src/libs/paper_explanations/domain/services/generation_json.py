import json
import math
from decimal import Decimal

from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError


class GenerationJson:
    @staticmethod
    def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    @staticmethod
    def _constant(value: str) -> None:
        raise ValueError('non-finite JSON number')

    @classmethod
    def _validate_tree(cls, value: object, depth: int = 0) -> None:
        if depth > 64:
            raise ValueError('JSON nesting limit')
        if isinstance(value, str):
            value.encode('utf-8')
        elif isinstance(value, dict):
            for key, item in value.items():
                cls._validate_tree(key, depth + 1)
                cls._validate_tree(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                cls._validate_tree(item, depth + 1)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError('non-finite JSON number')
        elif isinstance(value, Decimal):
            if not value.is_finite() or (value and not -30 <= value.adjusted() <= 15):
                raise ValueError('JSON decimal limit')

    @classmethod
    def object(cls, value: str | bytes, code: str, *, limit: int, decimals: bool = False) -> dict:
        try:
            if isinstance(value, bytes):
                if not 1 <= len(value) <= limit:
                    raise ValueError('JSON size limit')
                value = value.decode('utf-8')
            if not isinstance(value, str) or not 1 <= len(value.encode('utf-8')) <= limit:
                raise ValueError('JSON size limit')
            result = json.loads(
                value, object_pairs_hook=cls._unique, parse_constant=cls._constant,
                parse_float=Decimal if decimals else float,
            )
            if not isinstance(result, dict):
                raise ValueError('JSON object required')
            cls._validate_tree(result)
            return result
        except (ValueError, TypeError, RecursionError):
            raise ModelGatewayError(code) from None

    @staticmethod
    def canonical(value: object, code: str) -> str:
        try:
            text = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False
            )
            text.encode('utf-8')
            return text
        except (ValueError, TypeError, RecursionError):
            raise ModelGatewayError(code) from None
