"""INCY deep link cryptographic encoding and decoding.

Implements @incy/link-encoder@1.0.0 / crypt1 specification for generating
obfuscated deep links (incy://crypt1/<payload>) compatible with INCY iOS,
Android, and Desktop clients.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import TypedDict
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Salt parts: "incy", "deep", "crypt1", "v2026.06"
_SALT_P1 = b"incy"
_SALT_P2 = b"deep"
_SALT_P3 = b"crypt1"
_SALT_P4 = b"v2026.06"
_SALT = _SALT_P1 + _SALT_P2 + _SALT_P3 + _SALT_P4

_KEYMAT_A_OFFSET = 1024
_KEYMAT_B_OFFSET = 2048
_KEYMAT_LEN = 32

EXPECTED_KEY_FINGERPRINT = "b6bf708471cc90043232967660aade86a50b4e57929db2e53c5fa34db624c08c"
SCHEME = "incy"
HOST = "crypt1"
LINK_PREFIX = f"{SCHEME}://{HOST}/"

IV_LEN = 12
TAG_LEN = 16

KEYMAT_A_B64 = (
    "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"
    "/uhUhlhhc68cbET+hA=="
)

KEYMAT_B_B64 = (
    "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"
    "uEKz7yGFjNrxVsIow=="
)

_key_cache: bytes | None = None


def derive_key() -> bytes:
    """Derive the 32-byte AES-256-GCM key K1 and verify its fingerprint."""
    global _key_cache
    if _key_cache is not None:
        return _key_cache

    a = base64.b64decode(KEYMAT_A_B64)
    b = base64.b64decode(KEYMAT_B_B64)

    slice_a = a[_KEYMAT_A_OFFSET : _KEYMAT_A_OFFSET + _KEYMAT_LEN]
    slice_b = b[_KEYMAT_B_OFFSET : _KEYMAT_B_OFFSET + _KEYMAT_LEN]

    seed = _SALT + slice_a + slice_b
    k1 = hashlib.sha256(seed).digest()
    fingerprint = hashlib.sha256(k1).hexdigest()

    if fingerprint != EXPECTED_KEY_FINGERPRINT:
        raise ValueError(
            f"INCY K1 fingerprint mismatch (expected {EXPECTED_KEY_FINGERPRINT}, got {fingerprint})"
        )

    _key_cache = k1
    return k1


class DecryptedLink(TypedDict):
    url: str
    name: str | None


def b64url_encode(data: bytes) -> str:
    """Base64URL encode without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    """Base64URL decode with padding restoration."""
    pad = len(s) % 4
    if pad != 0:
        s += "=" * (4 - pad)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def build_plaintext(url: str, name: str | None = None) -> bytes:
    """Build canonical sorted UTF-8 JSON plaintext."""
    if not url or not isinstance(url, str):
        raise ValueError("url must be a non-empty string")

    payload: dict[str, str | int] = {"url": url, "v": 1}
    if name:
        payload["n"] = name[:128]

    # Deterministic compact JSON with sorted keys
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def encrypt_link(url: str, name: str | None = None) -> str:
    """
    Encrypt a subscription URL into an incy://crypt1/<payload> deep link.
    """
    url = url.strip()
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("url must be an absolute URL")
    if parsed.scheme != "https":
        raise ValueError("url must use https scheme")

    iv = os.urandom(IV_LEN)
    return encrypt_link_deterministic(url, name=name, iv=iv)


def encrypt_link_deterministic(url: str, name: str | None = None, *, iv: bytes) -> str:
    """Deterministic encryption helper using caller-provided 12-byte IV."""
    if len(iv) != IV_LEN:
        raise ValueError(f"IV must be exactly {IV_LEN} bytes")

    key = derive_key()
    plaintext = build_plaintext(url, name=name)

    aesgcm = AESGCM(key)
    # In cryptography AESGCM, encrypt returns ct + 16-byte tag
    ct_tag = aesgcm.encrypt(iv, plaintext, None)
    wire = iv + ct_tag

    return f"{LINK_PREFIX}{b64url_encode(wire)}"


def decrypt_link(link: str) -> DecryptedLink:
    """
    Decrypt an incy://crypt1/<payload> deep link.
    Throws ValueError on malformed link or tag authentication failure.
    """
    link = link.strip()
    if not link.startswith(LINK_PREFIX):
        raise ValueError(f"Link must start with {LINK_PREFIX}")

    payload_str = link[len(LINK_PREFIX) :].rstrip("/")
    if not payload_str:
        raise ValueError("Empty link payload")

    wire = b64url_decode(payload_str)
    if len(wire) < IV_LEN + TAG_LEN + 1:
        raise ValueError("Payload too short")

    iv = wire[:IV_LEN]
    ct_tag = wire[IV_LEN:]

    key = derive_key()
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(iv, ct_tag, None)
    except Exception as exc:
        raise ValueError("Authentication failed or invalid ciphertext") from exc

    try:
        data = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Malformed JSON plaintext") from exc

    if not isinstance(data, dict) or "url" not in data:
        raise ValueError("Missing url field in payload")

    return {
        "url": str(data["url"]),
        "name": str(data["n"]) if data.get("n") else None,
    }
