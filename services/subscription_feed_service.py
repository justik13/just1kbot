import base64
from datetime import timedelta
import logging
from typing import Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import User, VPNProfile
from database.repositories.profiles_repo import get_user_profiles
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.vpn_parser import build_conf_file

logger = logging.getLogger(__name__)

# Проект работает ТОЛЬКО с AmneziaWG (обычный WireGuard исключён из-за блокировок DPI).
# В текущей реализации активен исключительно протокол AWG 2.0 ("amneziawg2").
# Планы развития (Roadmap): поддержка AWG 3.0 ("amneziawg3") и стека Xray (VLESS-Reality и др.).
SUPPORTED_SUBSCRIPTION_PROTOCOLS = {"amneziawg2"}


class SubscriptionFeedService:
    @staticmethod
    async def get_exportable_configs(
        session: AsyncSession, user_id: int
    ) -> list[Tuple[VPNProfile, str]]:
        profiles = await get_user_profiles(session, user_id, include_deleting=False)
        exportable = []

        for p in profiles:
            if not p.is_active or not p.desired_is_active:
                continue
            if not p.raw_config:
                continue
            if not p.server or not p.server.is_active:
                continue
            if p.server.protocol not in SUPPORTED_SUBSCRIPTION_PROTOCOLS:
                continue

            try:
                conf = build_conf_file(p.raw_config)
                if not conf or not conf.strip():
                    continue
            except Exception as e:
                logger.warning(
                    "Profile %s has invalid raw_config, skipping export: %s",
                    p.id,
                    type(e).__name__,
                )
                continue

            exportable.append((p, conf))

        return exportable

    @staticmethod
    async def get_user_traffic(
        session: AsyncSession, user_id: int
    ) -> Tuple[int, int]:
        stmt = select(
            func.coalesce(func.sum(VPNProfile.traffic_up), 0),
            func.coalesce(func.sum(VPNProfile.traffic_down), 0),
        ).where(VPNProfile.user_id == user_id)
        result = await session.execute(stmt)
        up, down = result.one()
        return int(up or 0), int(down or 0)

    @classmethod
    async def build_feed(
        cls, session: AsyncSession, user: User
    ) -> Tuple[int, dict[str, str], str]:
        settings = get_settings()
        access_granted = SubscriptionService.check_vpn_access(user)
        upload_sum, download_sum = await cls.get_user_traffic(session, user.id)

        now = now_utc()
        if access_granted and user.subscription_end:
            if user.subscription_end > now:
                expire_unix = int(user.subscription_end.timestamp())
            else:
                # User is in VPN grace period (+4 hours)
                expire_unix = int(
                    (user.subscription_end + timedelta(hours=4)).timestamp()
                )
        else:
            expire_unix = (
                int(user.subscription_end.timestamp())
                if user.subscription_end
                else 0
            )

        support_username = (settings.SUPPORT_USERNAME or "support").lstrip("@")
        desc_text = f"Личные устройства | @{support_username}"
        announce_text = "Управление устройствами в Telegram"

        desc_b64 = base64.b64encode(desc_text.encode("utf-8")).decode("ascii")
        announce_b64 = base64.b64encode(announce_text.encode("utf-8")).decode("ascii")

        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "profile-title": "JUST1K VPN",
            "profile-description": f"base64:{desc_b64}",
            "announce": f"base64:{announce_b64}",
            "profile-update-interval": "3",
            "support-url": f"https://t.me/{support_username}",
            "subscription-userinfo": f"upload={upload_sum};download={download_sum};total=0;expire={expire_unix}",
        }

        if not access_granted:
            return 200, headers, ""

        configs = await cls.get_exportable_configs(session, user.id)
        if not configs:
            return 200, headers, ""

        lines = []
        for p, conf in configs:
            try:
                b64_conf = base64.urlsafe_b64encode(
                    conf.encode("utf-8")
                ).decode("ascii")

                flag = (p.server.country_flag or "").strip() if p.server else ""
                server_name = (p.server.name or "Server").strip() if p.server else "Server"
                device_name = (p.device_name or f"Device #{p.id}").strip()
                prefix = f"{flag} {server_name}".strip() if flag else server_name
                fragment = f"{prefix} — {device_name}"

                lines.append(f"amneziawg://{b64_conf}#{fragment}")
            except Exception as e:
                logger.warning(
                    "Error formatting amneziawg line for profile %s: %s",
                    p.id,
                    type(e).__name__,
                )
                continue

        if not lines:
            return 200, headers, ""

        feed_payload = "\n".join(lines)
        body = base64.b64encode(feed_payload.encode("utf-8")).decode("ascii")
        return 200, headers, body
