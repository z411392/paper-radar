import ast
import math
import struct

from libs.retrieval.exceptions.embedding_batch_error import EmbeddingBatchError


class NpyFloat32BatchCodec:
    MAX_CONTENT_BYTES = 128_000_000
    _MAGIC = b"\x93NUMPY"
    _VERSION = b"\x01\x00"
    _PREFIX_SIZE = 10

    @classmethod
    def encode(
        cls,
        rows: tuple[tuple[float, ...], ...],
        *,
        dimension: int,
    ) -> bytes:
        if (
            not isinstance(rows, tuple)
            or not rows
            or type(dimension) is not int
            or not 1 <= dimension <= 1_000_000
        ):
            raise EmbeddingBatchError("invalid_embedding_vector")
        data_bytes = len(rows) * dimension * 4
        if data_bytes > cls.MAX_CONTENT_BYTES:
            raise EmbeddingBatchError("embedding_batch_too_large")
        packed_rows = []
        for row in rows:
            if not isinstance(row, tuple) or len(row) != dimension:
                raise EmbeddingBatchError("embedding_dimension_mismatch")
            packed = bytearray()
            nonzero = False
            for value in row:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise EmbeddingBatchError("invalid_embedding_vector")
                try:
                    item = struct.pack("<f", float(value))
                    converted = struct.unpack("<f", item)[0]
                except (OverflowError, struct.error):
                    raise EmbeddingBatchError("invalid_embedding_vector") from None
                if not math.isfinite(converted):
                    raise EmbeddingBatchError("invalid_embedding_vector")
                nonzero = nonzero or converted != 0.0
                packed.extend(item)
            if not nonzero:
                raise EmbeddingBatchError("embedding_zero_vector")
            packed_rows.append(bytes(packed))

        header_text = (
            "{'descr': '<f4', 'fortran_order': False, "
            f"'shape': ({len(rows)}, {dimension}), }}"
        ).encode("ascii")
        padding = (
            -(
                cls._PREFIX_SIZE
                + len(header_text)
                + 1
            )
        ) % 64
        header = header_text + b" " * padding + b"\n"
        if len(header) > 65535:
            raise EmbeddingBatchError("embedding_npy_header_too_large")
        prefix = (
            cls._MAGIC
            + cls._VERSION
            + struct.pack("<H", len(header))
        )
        return prefix + header + b"".join(packed_rows)

    @classmethod
    def decode(cls, content: bytes) -> tuple[tuple[float, ...], ...]:
        if (
            not isinstance(content, bytes)
            or len(content) < cls._PREFIX_SIZE + 1
            or len(content) > cls.MAX_CONTENT_BYTES + 65535
            or content[:6] != cls._MAGIC
            or content[6:8] != cls._VERSION
        ):
            raise EmbeddingBatchError("invalid_embedding_npy")
        header_length = struct.unpack("<H", content[8:10])[0]
        header_end = cls._PREFIX_SIZE + header_length
        if (
            header_length < 1
            or header_end > len(content)
            or content[header_end - 1 : header_end] != b"\n"
            or header_end % 64 != 0
        ):
            raise EmbeddingBatchError("invalid_embedding_npy")
        try:
            header = ast.literal_eval(
                content[cls._PREFIX_SIZE:header_end].decode("ascii").strip()
            )
        except (ValueError, SyntaxError, UnicodeDecodeError):
            raise EmbeddingBatchError("invalid_embedding_npy") from None
        if (
            not isinstance(header, dict)
            or set(header) != {"descr", "fortran_order", "shape"}
            or header["descr"] != "<f4"
            or header["fortran_order"] is not False
            or not isinstance(header["shape"], tuple)
            or len(header["shape"]) != 2
        ):
            raise EmbeddingBatchError("invalid_embedding_npy")
        count, dimension = header["shape"]
        if (
            type(count) is not int
            or type(dimension) is not int
            or count < 1
            or dimension < 1
        ):
            raise EmbeddingBatchError("invalid_embedding_npy")
        payload = content[header_end:]
        expected_bytes = count * dimension * 4
        if len(payload) != expected_bytes:
            raise EmbeddingBatchError("invalid_embedding_npy")
        values = struct.unpack("<" + "f" * (count * dimension), payload)
        rows = []
        for index in range(count):
            row = tuple(
                values[index * dimension : (index + 1) * dimension]
            )
            if any(not math.isfinite(value) for value in row):
                raise EmbeddingBatchError("invalid_embedding_npy")
            if not any(value != 0.0 for value in row):
                raise EmbeddingBatchError("invalid_embedding_npy")
            rows.append(row)
        return tuple(rows)
