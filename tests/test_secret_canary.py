"""Test SC-04 secret canary redaction."""

import uuid

from eap.config import Settings


def test_canary_is_never_leaked_in_repr():
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    settings = Settings(
        EAP_SECRET_CANARY=canary,
        POSTGRES_PASSWORD="my-super-secret-password",
    )
    s_repr = repr(settings)
    assert canary not in s_repr, "Canary leaked in settings repr"
    assert "my-super-secret-password" not in s_repr, "Password leaked in settings repr"
