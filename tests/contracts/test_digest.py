"""RFC 8785 JCS and action digest tests (SC-01 / P0-WP2).

Verifies the action digest against the template requirement:
expected: 76580e7cecb951cc2e84d499b6006492bd394b4b44c068372464aedf2d30a436.
Also validates RFC 8785 canonicalization test vectors:
- ECMAScript number formatting and zero handling
- UTF-16 code-unit key ordering (differing from UTF-8 code-point byte ordering)
- Strict string escaping
"""

from __future__ import annotations

import json
from pathlib import Path

from eap.contracts.digest import action_digest, jcs, sha256_digest
from eap.contracts.models import ActionProposal

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_template_action_proposal_digest() -> None:
    template_path = REPO_ROOT / "templates" / "actions" / "action-proposal.example.json"
    template_data = json.loads(template_path.read_text(encoding="utf-8"))

    # Compute digest from dict
    computed_digest = action_digest(template_data)
    expected_digest = "76580e7cecb951cc2e84d499b6006492bd394b4b44c068372464aedf2d30a436"
    assert computed_digest == expected_digest, f"Digest mismatch: {computed_digest}"
    assert template_data["action_digest"] == expected_digest

    # Compute digest from Pydantic model
    proposal_model = ActionProposal.model_validate(template_data)
    model_digest = action_digest(proposal_model)
    assert model_digest == expected_digest

    print(f"\n[DIGEST OK] Template digest: {computed_digest}")


def test_retry_proposal_action_digest() -> None:
    retry_path = REPO_ROOT / "examples" / "action-proposal-retry.json"
    retry_data = json.loads(retry_path.read_text(encoding="utf-8"))
    assert action_digest(retry_data) == retry_data["action_digest"]


def test_rfc8785_number_vectors() -> None:
    # 0 and -0.0 serialize to '0'
    assert jcs(0) == b"0"
    assert jcs(-0.0) == b"0"

    # Integers up to 2^53 - 1
    assert jcs(1000000) == b"1000000"
    assert jcs(9007199254740991) == b"9007199254740991"

    # Floats with exponents within safe ranges
    assert jcs(1e20) == b"100000000000000000000"
    assert jcs(1e21) == b"1e+21"
    assert jcs(1e-6) == b"0.000001"
    assert jcs(1e-7) == b"1e-7"
    assert jcs(0.000001) == b"0.000001"


def test_rfc8785_key_ordering_utf16() -> None:
    # In RFC 8785 Section 3.2.3:
    # Keys are sorted according to UTF-16 code units.
    # U+FFFF has UTF-16: [0xFFFF]. UTF-8: 0xEF 0xBF 0xBF.
    # U+1F600 (grinning face) has UTF-16 surrogate pair: [0xD83D, 0xDE00]. UTF-8: 0xF0 0x9F 0x98 0x80.
    # In UTF-8 byte comparison, U+FFFF (0xEF) < U+1F600 (0xF0).
    # In UTF-16 code unit comparison, U+1F600 (0xD83D) < U+FFFF (0xFFFF)!
    data = {"\uffff": 1, "\U0001f600": 2}
    canonical = jcs(data).decode("utf-8")
    assert canonical == '{"\U0001f600":2,"\uffff":1}', (
        f"Key ordering did not follow UTF-16 code unit order! Got: {canonical}"
    )


def test_rfc8785_string_escaping_vectors() -> None:
    # Control characters 0x00 to 0x1F are escaped
    # Quote and backslash are escaped
    test_str = "line\nbreak\ttab\rreturn\"quote\\slash\u0000null"
    data = {"k": test_str}
    canonical = jcs(data).decode("utf-8")
    assert canonical == '{"k":"line\\nbreak\\ttab\\rreturn\\"quote\\\\slash\\u0000null"}'


def test_sha256_digest_utility() -> None:
    assert sha256_digest("test") == "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    assert sha256_digest(b"test") == "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
