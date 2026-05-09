"""API key generation, hashing, and verification.

Keys look like `aegis_<env>_<32-char base32>`. We store:
  - The full key, bcrypt-hashed → `key_hash`
  - The first 12 chars (`aegis_live_X`) → `prefix`, used as a lookup index

The plaintext is shown to the user once on creation and never again.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import bcrypt

_PREFIX_LEN = 12
_RANDOM_BYTES = 24  # → 32 base32 chars after encoding


@dataclass(frozen=True)
class GeneratedKey:
    plaintext: str
    prefix: str
    key_hash: str


def generate_api_key(env: str = "live") -> GeneratedKey:
    """Mint a fresh API key. The plaintext is only ever returned here — store the hash."""
    raw = secrets.token_urlsafe(_RANDOM_BYTES).rstrip("=")
    plaintext = f"aegis_{env}_{raw}"
    key_hash = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=10)).decode()
    return GeneratedKey(plaintext=plaintext, prefix=plaintext[:_PREFIX_LEN], key_hash=key_hash)


def extract_prefix(plaintext: str) -> str:
    """Return the lookup prefix for an inbound key."""
    return plaintext[:_PREFIX_LEN]


def verify_key(plaintext: str, key_hash: str) -> bool:
    """Constant-time-ish bcrypt verify."""
    try:
        return bcrypt.checkpw(plaintext.encode(), key_hash.encode())
    except (ValueError, TypeError):
        return False
