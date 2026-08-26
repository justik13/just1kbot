import hashlib
import hmac
import re

from config.settings import get_settings
from integrations.amnezia_bridge.constants import (
    BRIDGE_TOKEN_MAX_FUTURE_SKEW_SECONDS,
    BRIDGE_TOKEN_TTL_SECONDS,
    BRIDGE_TOKEN_VERSION,
)
from utils.datetime_helpers import now_utc

SIG_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AmneziaBridgeTokenService:
    """Encapsulated token signer, verifier, and URL builder for Amnezia Web Bridge."""

    @staticmethod
    def get_canonical_string(profile_id: int, user_id: int, exp: int) -> str:
        return f"amnezia:{BRIDGE_TOKEN_VERSION}:{profile_id}:{user_id}:{exp}"

    @classmethod
    def get_secret(cls) -> str:
        secret = get_settings().AMNEZIA_BRIDGE_HMAC_SECRET
        if not secret:
            raise RuntimeError("AMNEZIA_BRIDGE_HMAC_SECRET is not configured")
        return secret

    @classmethod
    def is_enabled(cls) -> bool:
        return bool(get_settings().AMNEZIA_BRIDGE_HMAC_SECRET)

    @classmethod
    def sign(
        cls,
        profile_id: int,
        user_id: int,
        exp: int,
        secret: str | None = None,
    ) -> str:
        key = (secret if secret is not None else cls.get_secret()).encode("utf-8")
        canonical = cls.get_canonical_string(profile_id, user_id, exp).encode("utf-8")
        return hmac.new(key, canonical, hashlib.sha256).hexdigest().lower()

    @classmethod
    def verify(
        cls,
        profile_id: int,
        user_id: int,
        exp: int,
        sig: str,
        secret: str | None = None,
    ) -> bool:
        if not sig or not isinstance(sig, str) or not SIG_PATTERN.fullmatch(sig):
            return False

        expected = cls.sign(profile_id, user_id, exp, secret=secret)
        return hmac.compare_digest(expected, sig)

    @classmethod
    def is_ttl_valid(cls, exp: int, now_ts: int | None = None) -> tuple[bool, str]:
        """Check bidirectional TTL validity.

        Returns (is_valid, reason) where reason is 'valid', 'expired' (410), or 'future_skew_exceeded' (403).
        """
        if now_ts is None:
            now_ts = int(now_utc().timestamp())

        if exp <= now_ts:
            return False, "expired"
        if exp > now_ts + BRIDGE_TOKEN_TTL_SECONDS + BRIDGE_TOKEN_MAX_FUTURE_SKEW_SECONDS:
            return False, "future_skew_exceeded"
        return True, "valid"

    @classmethod
    def build_bridge_url(
        cls,
        domain: str,
        profile_id: int,
        user_id: int,
        ttl_seconds: int = BRIDGE_TOKEN_TTL_SECONDS,
        secret: str | None = None,
    ) -> str:
        now_ts = int(now_utc().timestamp())
        exp = now_ts + ttl_seconds
        sig = cls.sign(profile_id, user_id, exp, secret=secret)
        clean_domain = domain.strip().rstrip("/")
        return f"https://{clean_domain}/amnezia/open/{profile_id}?uid={user_id}&exp={exp}&sig={sig}"
