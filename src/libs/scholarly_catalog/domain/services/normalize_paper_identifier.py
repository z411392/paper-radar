import re

from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class NormalizePaperIdentifier:
    @staticmethod
    def _text(value: object, *, maximum_bytes: int) -> str:
        if not isinstance(value, str):
            raise PaperIdentityError("invalid_identifier")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise PaperIdentityError("invalid_identifier") from exc
        value = value.strip()
        if not value or size > maximum_bytes or any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise PaperIdentityError("invalid_identifier")
        return value

    @staticmethod
    def _strip_known_prefix(value: str, prefixes: tuple[str, ...]) -> str | None:
        lowered = value.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                return value[len(prefix) :]
        return None

    @classmethod
    def _doi(cls, value: str) -> NormalizedIdentifier:
        raw = value
        prefixed = cls._strip_known_prefix(
            raw,
            (
                "https://doi.org/",
                "http://doi.org/",
                "https://dx.doi.org/",
                "http://dx.doi.org/",
            ),
        )
        if prefixed is not None:
            raw = prefixed
        elif raw[:4].lower() == "doi:":
            raw = raw[4:].strip()
        elif "://" in raw:
            raise PaperIdentityError("invalid_identifier")
        raw = raw.strip()
        if (
            not raw
            or "?" in raw
            or "#" in raw
            or "%" in raw
            or len(raw.encode("utf-8")) > 512
            or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in raw)
            or re.fullmatch(r"10\.\d{4,9}/[^\s]+", raw, flags=re.IGNORECASE) is None
        ):
            raise PaperIdentityError("invalid_identifier")
        return NormalizedIdentifier("doi", raw.lower(), None)

    @classmethod
    def _arxiv(cls, value: str) -> NormalizedIdentifier:
        raw = value
        prefixed = cls._strip_known_prefix(
            raw,
            ("https://arxiv.org/abs/", "http://arxiv.org/abs/"),
        )
        if prefixed is not None:
            raw = prefixed
        elif raw[:6].lower() == "arxiv:":
            raw = raw[6:]
        elif "://" in raw:
            raise PaperIdentityError("invalid_identifier")
        raw = raw.strip()
        try:
            raw.encode("ascii")
        except UnicodeEncodeError as exc:
            raise PaperIdentityError("invalid_identifier") from exc
        if (
            not raw
            or "?" in raw
            or "#" in raw
            or "%" in raw
            or len(raw) > 128
            or any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in raw)
        ):
            raise PaperIdentityError("invalid_identifier")

        modern = re.fullmatch(r"(\d{4})\.(\d{4,5})(?:v([1-9]\d*))?", raw)
        if modern is not None:
            yymm, serial, version = modern.groups()
            month = int(yymm[2:4])
            if not 1 <= month <= 12:
                raise PaperIdentityError("invalid_identifier")
            period = int(yymm)
            expected_digits = 4 if 704 <= period <= 1412 else 5 if period >= 1501 else 0
            if expected_digits == 0 or len(serial) != expected_digits:
                raise PaperIdentityError("invalid_identifier")
            return NormalizedIdentifier("arxiv", f"{yymm}.{serial}", version)

        legacy = re.fullmatch(r"([a-z][A-Za-z0-9.-]*)/(\d{7})(?:v([1-9]\d*))?", raw)
        if legacy is None:
            raise PaperIdentityError("invalid_identifier")
        archive, serial, version = legacy.groups()
        if not 1 <= int(serial[2:4]) <= 12:
            raise PaperIdentityError("invalid_identifier")
        return NormalizedIdentifier("arxiv", f"{archive}/{serial}", version)

    @classmethod
    def _pmid(cls, value: str) -> NormalizedIdentifier:
        raw = value.strip()
        if re.fullmatch(r"[0-9]{1,10}", raw) is None or int(raw) <= 0:
            raise PaperIdentityError("invalid_identifier")
        return NormalizedIdentifier("pmid", str(int(raw)), None)

    @classmethod
    def _pmc(cls, value: str) -> NormalizedIdentifier:
        raw = value.strip().upper()
        match = re.fullmatch(r"PMC([0-9]{1,10})", raw)
        if match is None or int(match[1]) <= 0:
            raise PaperIdentityError("invalid_identifier")
        return NormalizedIdentifier("pmc", "PMC" + str(int(match[1])), None)

    def __call__(self, namespace: str, value: str) -> NormalizedIdentifier:
        if namespace == "doi":
            return self._doi(self._text(value, maximum_bytes=2048))
        if namespace == "arxiv":
            return self._arxiv(self._text(value, maximum_bytes=512))
        if namespace == "pmid":
            return self._pmid(self._text(value, maximum_bytes=32))
        if namespace == "pmc":
            return self._pmc(self._text(value, maximum_bytes=32))
        raise PaperIdentityError("unsupported_identifier_namespace")
