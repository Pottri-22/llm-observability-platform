"""Unit tests for API key generation + verification."""

from __future__ import annotations

from app.auth.api_key import extract_prefix, generate_api_key, verify_key


def test_generated_key_format() -> None:
    gen = generate_api_key(env="test")
    assert gen.plaintext.startswith("aegis_test_")
    assert len(gen.plaintext) > 20
    assert gen.prefix == gen.plaintext[:12]
    assert gen.key_hash != gen.plaintext  # must be hashed, not stored plaintext
    assert gen.key_hash.startswith("$2")  # bcrypt prefix


def test_verify_correct_key() -> None:
    gen = generate_api_key(env="live")
    assert verify_key(gen.plaintext, gen.key_hash) is True


def test_verify_wrong_key() -> None:
    gen = generate_api_key(env="live")
    assert verify_key("aegis_live_wrong", gen.key_hash) is False


def test_verify_with_corrupt_hash_returns_false_not_raise() -> None:
    assert verify_key("aegis_live_anything", "not-a-valid-bcrypt-hash") is False


def test_each_generation_unique() -> None:
    a = generate_api_key()
    b = generate_api_key()
    assert a.plaintext != b.plaintext
    assert a.key_hash != b.key_hash


def test_extract_prefix_matches_generated() -> None:
    gen = generate_api_key()
    assert extract_prefix(gen.plaintext) == gen.prefix
