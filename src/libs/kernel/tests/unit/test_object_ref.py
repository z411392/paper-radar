from dataclasses import replace

import pytest

from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.exceptions.storage_error import StorageError


def valid_ref():
    digest = "a" * 64
    return ObjectRef(
        "raw:" + digest, digest, "objects/raw/aa/" + digest, "raw", "text/plain", 1, "fixed", "retain"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "../raw"},
        {"content_sha256": "x" * 64},
        {"content_sha256": "a" * 63},
        {"relative_path": "../outside"},
        {"relative_path": "/absolute"},
        {"object_id": "forged"},
        {"byte_size": -1},
        {"byte_size": True},
        {"media_type": ""},
        {"retention_policy": " "},
        {"media_type": "text/plain\r\nX: inject"},
        {"state": "unknown"},
    ],
)
def test_malformed_reference_is_rejected(changes):
    with pytest.raises(StorageError, match="invalid_object"):
        replace(valid_ref(), **changes)
