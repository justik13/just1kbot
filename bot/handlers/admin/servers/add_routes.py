import logging
import urllib.parse

from config.constants import (
    AMNEZIA_PROTOCOL,
    AMNEZIA_PROTOCOLS,
    AdminAuditAction,
)
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.repositories.servers_repo import (
    create_server,
    get_server_by_api_url,
)
from services.amnezia_client import AmneziaClient
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.security import is_safe_url
from utils.telegram import render_hub, safe

from .common import URL_REGEX, normalize_api_url

from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()
logger = logging.getLogger(__name__)

SAFE_DEFAULT_MAX_PEERS = 200


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.button(text=texts.ADMIN_SERVER_BTN_PROTO_AWG, callback_data="admin_server_add_proto:amneziawg2")
    builder.button(text=texts.ADMIN_SERVER_BTN_PROTO_XRAY, callback_data="admin_server_add_proto:xray")
    builder.button(text=texts.ADMIN_BTN_BACK_TO_SERVERS, callback_data="admin_servers")
    builder.adjust(1, 1, 1)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_SELECT_PROTO_PROMPT,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_server_add_proto:"))
async def select_add_server_protocol(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    protocol = callback.data.split(":")[1]
    await callback.answer(show_alert=False)
    await state.set_state(AdminStates.adding_server)
    await state.update_data(protocol=protocol, step="name")

    await callback.message.edit_text(
        texts.ADMIN_SERVER_NAME_PROMPT,
        reply_markup=get_back_button("admin_server_add"),
        parse_mode="HTML",
    )


@router.message(AdminStates.adding_server)
async def process_add_server(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    try:
        await message.delete()
    except Exception:
        pass

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    trigger_msg_id = getattr(message, "message_id", None)

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers"),
            trigger_message_id=trigger_msg_id,
        )
        return

    if message.text.startswith("/"):
        await state.clear()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers"),
            trigger_message_id=trigger_msg_id,
        )
        return

    data = await state.get_data()
    step = data.get("step")

    if step == "name":
        name = message.text.strip()

        if len(name) > 50:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_NAME_TOO_LONG.format(max=50),
                get_back_button("admin_servers"),
                trigger_message_id=trigger_msg_id,
            )
            return

        await state.update_data(name=name, step="flag")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_FLAG_PROMPT,
            get_back_button("admin_servers"),
            trigger_message_id=trigger_msg_id,
        )

    elif step == "flag":
        country_flag = message.text.strip()

        if len(country_flag) > 10:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ADMIN_SERVER_FLAG_TOO_LONG,
                get_back_button("admin_servers"),
                trigger_message_id=trigger_msg_id,
            )
            return

        await state.update_data(
            country_flag=country_flag,
            step="api_url",
        )

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_URL_PROMPT,
            get_back_button("admin_servers"),
            trigger_message_id=trigger_msg_id,
        )

    elif step == "api_url":
        api_url = normalize_api_url(message.text)

        if len(api_url) > 500:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_URL_TOO_LONG.format(max=500),
                get_back_button("admin_servers"),
                trigger_message_id=trigger_msg_id,
            )
            return

        if not URL_REGEX.match(api_url):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_INVALID_URL,
                get_back_button("admin_servers"),
                parse_mode="HTML",
                trigger_message_id=trigger_msg_id,
            )
            return

        if not await is_safe_url(api_url):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ADMIN_SERVER_URL_FORBIDDEN,
                get_back_button("admin_servers"),
                parse_mode="HTML",
                trigger_message_id=trigger_msg_id,
            )
            return

        existing = await get_server_by_api_url(session, api_url)

        if existing:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_DUPLICATE_URL.format(
                    api_url=safe(api_url),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML",
                trigger_message_id=trigger_msg_id,
            )

            await state.clear()

            return

        await state.update_data(api_url=api_url, step="api_key")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_KEY_PROMPT,
            get_back_button("admin_servers"),
            trigger_message_id=trigger_msg_id,
        )

    elif step == "api_key":
        api_key = message.text.strip()

        if not api_key or len(api_key) < 8:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_API_KEY_SHORT.format(min=8),
                get_back_button("admin_servers"),
                trigger_message_id=trigger_msg_id,
            )
            return

        all_data = await state.get_data()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_CHECKING,
            get_back_button("admin_servers"),
            parse_mode="HTML",
            trigger_message_id=trigger_msg_id,
        )

        protocol = all_data.get("protocol", AMNEZIA_PROTOCOL)

        if protocol == "xray":
            from services.xray_node_client import XrayNodeClient

            xray_ok = False
            xray_epoch = None
            xray_data = None
            try:
                async with XrayNodeClient(timeout=10.0) as xray_client:
                    xray_ok, xray_epoch, xray_data = await xray_client.check_health(
                        all_data["api_url"], api_key
                    )
            except Exception as e:
                logger.warning("Xray health check failed for %s: %s", all_data["api_url"], e)
                xray_ok = False

            if not xray_ok:
                await render_hub(
                    message.bot,
                    message.chat.id,
                    texts.ERROR_SERVER_UNREACHABLE,
                    get_back_button("admin_servers"),
                    parse_mode="HTML",
                )
                await state.clear()
                return
            from config.constants import DEFAULT_XRAY_ORIGIN_MAX_CLIENTS
            api_server_name = all_data["name"]
            api_max_peers = DEFAULT_XRAY_ORIGIN_MAX_CLIENTS
            protocol_name = "xray"
            capabilities = []
            inbounds = (xray_data or {}).get("inbounds", [])
            has_vless = any(
                "vless" in (ib if isinstance(ib, str) else str(ib.get("tag", ""))).lower()
                for ib in inbounds
                if isinstance(ib, (str, dict))
            )
            has_wl = any(
                "wl" in (ib if isinstance(ib, str) else str(ib.get("tag", ""))).lower()
                for ib in inbounds
                if isinstance(ib, (str, dict))
            ) or bool((xray_data or {}).get("relays"))
            if has_vless:
                capabilities.append("vless")
            if has_wl:
                capabilities.append("xray_origin")
            if not capabilities:
                capabilities = ["xray_origin"]
            server = await create_server(
                session,
                name=api_server_name,
                country_flag=all_data["country_flag"],
                api_url=all_data["api_url"],
                api_key=api_key,
                protocol=protocol_name,
                max_clients=api_max_peers,
                capabilities=capabilities,
            )
            if xray_epoch:
                server.xray_instance_epoch = xray_epoch
            if xray_data:
                server.xray_instance_boot_id = xray_data.get("boot_id")
                server.xray_instance_starttime = xray_data.get("starttime")
                extra = dict(server.extra_data or {})
                if "secret_base_path" in xray_data:
                    extra["secret_base_path"] = xray_data["secret_base_path"]
                if "relays" in xray_data:
                    extra["relays"] = xray_data["relays"]
                if "cdn_domain" in xray_data and xray_data["cdn_domain"]:
                    extra["cdn_domain"] = xray_data["cdn_domain"]
                if "sub_path_prefix" in xray_data and xray_data["sub_path_prefix"]:
                    extra["sub_path_prefix"] = xray_data["sub_path_prefix"]
                if has_vless:
                    parsed_u = urllib.parse.urlsplit(all_data["api_url"])
                    if parsed_u.hostname:
                        extra.setdefault("domain", parsed_u.hostname)
                    extra.setdefault("vless_port", 443)
                    extra.setdefault("xray_api_url", all_data["api_url"])
                server.extra_data = extra

            await AuditService.log_action(
                session,
                message.from_user.id,
                AdminAuditAction.ADD_SERVER,
                "Server",
                server.id,
                api_server_name,
            )

            proto_label = texts.PROTOCOL_XRAY_ORIGIN if "xray_origin" in capabilities else texts.PROTOCOL_VLESS
            msg_text = texts.ADMIN_SERVER_ADDED.format(
                flag=all_data["country_flag"],
                name=safe(api_server_name),
                protocol=proto_label,
                max_clients=api_max_peers,
                api_url=safe(all_data["api_url"]),
            )
            relays = (server.extra_data or {}).get("relays", [])
            if "xray_origin" in capabilities and not relays:
                msg_text += texts.ADMIN_SERVER_ADDED_NO_RELAYS_WARNING

            await render_hub(
                message.bot,
                message.chat.id,
                msg_text,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            logger.info(
                f"Admin {message.from_user.id} added Xray server: {server.id}"
            )
            await state.clear()
            return

        client = AmneziaClient(
            all_data["api_url"],
            api_key,
        )

        if not await client.healthcheck():
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_UNREACHABLE,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )

            await state.clear()

            return

        server_info = await client.get_server_info()

        if not server_info:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_API_INFO_FAILED,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )

            await state.clear()

            return

        protocols = server_info.protocols

        if not any(p in AMNEZIA_PROTOCOLS for p in protocols):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_PROTOCOL_NOT_SUPPORTED.format(
                    protocols=safe(
                        ", ".join(protocols) if protocols else texts.LABEL_UNKNOWN_LOWER
                    ),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )

            await state.clear()

            return

        api_max_peers = server_info.get_effective_max_peers()

        if not (server_info.maxPeers or server_info.serverMaxPeers):
            logger.warning(
                "Amnezia API did not return max peers for %s. "
                "Using safe default %s instead of %s.",
                all_data["api_url"],
                SAFE_DEFAULT_MAX_PEERS,
                server_info.SERVER_MAX_PEERS,
            )

            api_max_peers = SAFE_DEFAULT_MAX_PEERS

        api_server_name = (all_data.get("name") or server_info.name or "AmneziaWG")[:50]

        existing = await get_server_by_api_url(
            session,
            all_data["api_url"],
        )

        if existing:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_DUPLICATE_URL.format(
                    api_url=safe(all_data["api_url"]),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )

            await state.clear()

            return

        server_protocol = server_info.get_protocol()

        capabilities = None
        extra_info = None
        # Auto-detect Dual Node: check if Xray VLESS API is also available on this node
        try:
            from services.xray_node_client import XrayNodeClient

            async with XrayNodeClient(timeout=3.0) as xray_client:
                x_ok, _, _ = await xray_client.check_health(all_data["api_url"], api_key)
                if x_ok:
                    capabilities = ["awg", "vless"]
                    parsed = urllib.parse.urlsplit(all_data["api_url"])
                    domain = parsed.hostname or ""
                    extra_info = {
                        "domain": domain,
                        "vless_port": 443,
                        "xray_api_url": all_data["api_url"],
                    }
        except Exception:
            pass

        create_kwargs = {
            "name": api_server_name,
            "country_flag": all_data["country_flag"],
            "api_url": all_data["api_url"],
            "api_key": api_key,
            "protocol": server_protocol,
            "max_clients": api_max_peers,
        }
        if capabilities:
            create_kwargs["capabilities"] = capabilities

        server = await create_server(
            session,
            **create_kwargs,
        )

        if extra_info:
            server.extra_data = {**(server.extra_data or {}), **extra_info}
            await session.flush()

        await AuditService.log_action(
            session,
            message.from_user.id,
            AdminAuditAction.ADD_SERVER,
            "Server",
            server.id,
            api_server_name,
        )

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_ADDED.format(
                flag=all_data["country_flag"],
                name=safe(api_server_name),
                protocol=server_protocol,
                max_clients=api_max_peers,
                api_url=safe(all_data["api_url"]),
            ),
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )

        logger.info(
            f"Admin {message.from_user.id} added server: {server.id}"
        )

        await state.clear()
