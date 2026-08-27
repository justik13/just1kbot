from bot import texts
import logging
import re

from aiohttp import web

from config.constants import AMNEZIA_PROTOCOL
from database.connection import session_scope
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.users_repo import get_user_by_id
from integrations.amnezia_bridge.constants import (
    MAX_BRIDGE_REQUEST_TARGET_BYTES,
    MAX_RAW_CONFIG_BYTES,
)
from integrations.amnezia_bridge.token_service import AmneziaBridgeTokenService
from integrations.amnezia_bridge.web_templates import (
    AMNEZIA_SECURITY_HEADERS,
    render_500_html,
    render_amnezia_bridge_html,
    render_error_html,
    render_expired_html,
)
from services.subscription import SubscriptionService
from utils.http_rate_limiter import amnezia_bridge_rate_limiter, get_trusted_client_ip
from utils.vpn_helpers import (
    InvalidAmneziaConfigError,
    InvalidAmneziaProfileError,
    build_display_vpn_uri,
)

logger = logging.getLogger(__name__)

NUM_PATTERN = re.compile(r"^[0-9]+$")
SIG_PATTERN = re.compile(r"^[0-9a-f]{64}$")


async def amnezia_bridge_handler(request: web.Request) -> web.Response:
    """1-Click Web Bridge endpoint for AmneziaVPN connection key delivery.

    Strict 20-step security and access validation pipeline.
    Zero-logging of credentials, keys, full query strings, or tracebacks.
    """
    try:
        if not AmneziaBridgeTokenService.is_enabled():
            logger.info("Amnezia bridge requested while feature is disabled")
            return web.Response(status=404, text="Not Found")

        # Step 1: Request size and ASCII byte length check
        raw_path = request.raw_path or ""
        if len(raw_path.encode("utf-8")) > MAX_BRIDGE_REQUEST_TARGET_BYTES:
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_NEKORREKTNYY_ZAPROS_51, texts.UI_WEB_ROUTES_RAZMER_ZAPROSA_PREVYSHAET_DOPU_51),
                status=400,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

        profile_id_str = request.match_info.get("profile_id", "")
        uid_str = request.query.get("uid", "")
        exp_str = request.query.get("exp", "")
        sig = request.query.get("sig", "")

        # Input size guards against DoS
        if len(profile_id_str) > 10 or len(uid_str) > 10 or len(exp_str) > 11:
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_NEKORREKTNYY_ZAPROS_64, texts.UI_WEB_ROUTES_PREVYSHENA_DLINA_PARAMETROV_ZA_64),
                status=400,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

        # Strict regex syntax validation
        if (
            not profile_id_str
            or not uid_str
            or not exp_str
            or not sig
            or not NUM_PATTERN.fullmatch(profile_id_str)
            or not NUM_PATTERN.fullmatch(uid_str)
            or not NUM_PATTERN.fullmatch(exp_str)
            or not SIG_PATTERN.fullmatch(sig)
        ):
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_NEKORREKTNYY_ZAPROS_81, texts.UI_WEB_ROUTES_NEVERNYY_FORMAT_PARAMETROV_ZAP_81),
                status=400,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

        profile_id = int(profile_id_str)
        uid = int(uid_str)
        exp = int(exp_str)

        # Range bounds
        if profile_id < 1 or profile_id > 2147483647 or uid < 1 or uid > 2147483647 or exp < 0 or exp > 4102444800:
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_NEKORREKTNYY_ZAPROS_93, texts.UI_WEB_ROUTES_ZNACHENIYA_PARAMETROV_VNE_DOPU_93),
                status=400,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

        # Step 2: Non-blocking rate limiting (before HMAC and DB)
        client_ip = get_trusted_client_ip(request)
        is_allowed, retry_after = amnezia_bridge_rate_limiter.check(client_ip)
        if not is_allowed:
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_SLISHKOM_MNOGO_ZAPROSOV_103, texts.UI_WEB_ROUTES_PREVYSHEN_LIMIT_ZAPROSOV_POZHA_103),
                status=429,
                headers={**AMNEZIA_SECURITY_HEADERS, "Retry-After": str(retry_after)},
            )

        # Step 3: Bidirectional TTL check
        is_ttl_valid, ttl_reason = AmneziaBridgeTokenService.is_ttl_valid(exp)
        if not is_ttl_valid:
            if ttl_reason == "expired":
                return web.Response(
                    text=render_expired_html(),
                    status=410,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_DOSTUP_ZAPRESHCHEN_118, texts.UI_WEB_ROUTES_NEKORREKTNOE_VREMYA_ZAPROSA_118),
                status=403,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

        # Step 4: HMAC signature verification
        if not AmneziaBridgeTokenService.verify(profile_id, uid, exp, sig):
            return web.Response(
                text=render_error_html(texts.UI_WEB_ROUTES_DOSTUP_ZAPRESHCHEN_126, texts.UI_WEB_ROUTES_NEDEYSTVITELNAYA_PODPIS_SSYLKI_126),
                status=403,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

        # Step 5: Database access and ACL checks
        async with session_scope() as session:
            profile = await get_profile_by_id(session, profile_id)
            if not profile:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_PROFIL_NE_NAYDEN_136, texts.UI_WEB_ROUTES_USTROYSTVO_NE_NAYDENO_136),
                    status=404,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            user = await get_user_by_id(session, uid)
            if not user:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_POLZOVATEL_NE_NAYDEN_144, texts.UI_WEB_ROUTES_POLZOVATEL_NE_NAYDEN_144),
                    status=404,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            # Ownership check
            if profile.user_id != user.id:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_DOSTUP_ZAPRESHCHEN_152, texts.UI_WEB_ROUTES_USTROYSTVO_PRINADLEZHIT_DRUGOM_152),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            # User account status checks
            if user.is_deleted:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_DOSTUP_ZAPRESHCHEN_160, texts.UI_WEB_ROUTES_AKKAUNT_UDALEN_160),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            if user.is_banned or user.financial_hold:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_DOSTUP_ZAPRESHCHEN_167, texts.UI_WEB_ROUTES_AKKAUNT_ZABLOKIROVAN_ILI_VREME_167),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            # Subscription entitlement check
            if not SubscriptionService.check_vpn_access(user):
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_PODPISKA_NE_AKTIVNA_175, texts.UI_WEB_ROUTES_PRODLITE_PODPISKU_V_TELEGRAM_B_175),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            # Server status & protocol checks
            server = profile.server
            if not server:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_OSHIBKA_SERVERA_184, texts.UI_WEB_ROUTES_SERVER_USTROYSTVA_NE_NAYDEN_184),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            if server.protocol != AMNEZIA_PROTOCOL:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_NEPODDERZHIVAEMYY_PROTOKOL_191, texts.UI_WEB_ROUTES_DANNAYA_FUNKTSIYA_DOSTUPNA_TOL_191),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            if not server.is_active:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_SERVER_VREMENNO_NEDOSTUPEN_198, texts.UI_WEB_ROUTES_SERVER_NAKHODITSYA_NA_TEKHNICH_198),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            # Profile lifecycle checks
            if not profile.is_active:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_USTROYSTVO_NE_AKTIVNO_206, texts.UI_WEB_ROUTES_USTROYSTVO_OTKLYUCHENO_V_TELEG_206),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            if profile.provisioning_status not in ("active", "pending_update", "update_failed"):
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_USTROYSTVO_NASTRAIVAETSYA_213, texts.UI_WEB_ROUTES_POZHALUYSTA_PODOZHDITE_ZAVERSH_213),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            if not profile.peer_id:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_KLYUCH_NE_GOTOV_220, texts.UI_WEB_ROUTES_KLYUCH_PODKLYUCHENIYA_ESHCHE_N_220),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            raw_config = (profile.raw_config or "").strip()
            if not raw_config or not raw_config.startswith("vpn://") or len(raw_config.encode("utf-8")) > MAX_RAW_CONFIG_BYTES:
                return web.Response(
                    text=render_error_html(texts.UI_WEB_ROUTES_OSHIBKA_KONFIGURATSII_228, texts.UI_WEB_ROUTES_NEKORREKTNYE_DANNYE_KONFIGURAT_228),
                    status=403,
                    headers=AMNEZIA_SECURITY_HEADERS,
                )

            # Build and customize display VPN URI
            display_vpn_uri = build_display_vpn_uri(profile)

            # Sanitized logging: log only successful fact
            logger.info("Amnezia bridge access granted: profile_id=%s user_id=%s", profile.id, user.id)

            server_name = (server.name or "Server").strip()
            device_name = (profile.device_name or f"Device #{profile.id}").strip()
            country_flag = (server.country_flag or "").strip()

            return web.Response(
                text=render_amnezia_bridge_html(
                    vpn_uri=display_vpn_uri,
                    server_name=server_name,
                    device_name=device_name,
                    country_flag=country_flag,
                ),
                status=200,
                headers=AMNEZIA_SECURITY_HEADERS,
            )

    except (InvalidAmneziaConfigError, InvalidAmneziaProfileError) as e:
        logger.warning("Amnezia bridge config error: %s", type(e).__name__)
        return web.Response(
            text=render_error_html(texts.UI_WEB_ROUTES_OSHIBKA_KONFIGURATSII_257, texts.UI_WEB_ROUTES_NE_UDALOS_SFORMIROVAT_NASTROYK_257),
            status=400,
            headers=AMNEZIA_SECURITY_HEADERS,
        )
    except Exception as exc:
        logger.error(
            "Unexpected error in Amnezia bridge handler: %s",
            type(exc).__name__,
        )
        return web.Response(
            text=render_500_html(),
            status=500,
            headers=AMNEZIA_SECURITY_HEADERS,
        )
