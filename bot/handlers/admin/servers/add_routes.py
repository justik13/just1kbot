import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.keyboards import get_back_button
from bot.states import AdminStates
from config.constants import AMNEZIA_PROTOCOL
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

    await callback.message.edit_text(
        texts.ADMIN_SERVER_NAME_PROMPT,
        reply_markup=get_back_button("admin_servers"),
    )

    await state.set_state(AdminStates.adding_server)
    await state.update_data(step="name")


@router.message(AdminStates.adding_server)
async def process_add_server(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers"),
        )
        return

    data = await state.get_data()
    step = data.get("step")

    if step == "name":
        name = message.text.strip()

        if len(name) > 255:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_NAME_TOO_LONG.format(max=255),
                get_back_button("admin_servers"),
            )
            return

        await state.update_data(name=name, step="flag")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_FLAG_PROMPT,
            get_back_button("admin_servers"),
        )

    elif step == "flag":
        country_flag = message.text.strip()

        if len(country_flag) > 10:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ADMIN_SERVER_FLAG_TOO_LONG,
                get_back_button("admin_servers"),
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
        )

    elif step == "api_url":
        api_url = normalize_api_url(message.text)

        if len(api_url) > 500:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_URL_TOO_LONG.format(max=500),
                get_back_button("admin_servers"),
            )
            return

        if not URL_REGEX.match(api_url):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_INVALID_URL,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            return

        if not await is_safe_url(api_url):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ADMIN_SERVER_URL_FORBIDDEN,
                get_back_button("admin_servers"),
                parse_mode="HTML",
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
            )

            await state.clear()

            return

        await state.update_data(api_url=api_url, step="api_key")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_KEY_PROMPT,
            get_back_button("admin_servers"),
        )

    elif step == "api_key":
        api_key = message.text.strip()
        try:
            await message.delete()
        except Exception as e:
            logger.warning("Failed to delete secret message %s: %s", message.message_id, e)


        if not api_key or len(api_key) < 8:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_API_KEY_SHORT.format(min=8),
                get_back_button("admin_servers"),
            )
            return

        all_data = await state.get_data()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_CHECKING,
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )

        from services.xray_node_client import XrayNodeClient

        is_xray = False
        xray_epoch = None
        xray_data = None
        try:
            async with XrayNodeClient(timeout=10.0) as xray_client:
                xray_ok, xray_epoch, xray_data = await xray_client.check_health(
                    all_data["api_url"], api_key
                )
                if xray_ok:
                    is_xray = True
        except Exception:
            is_xray = False

        if is_xray:
            from config.constants import DEFAULT_XRAY_ORIGIN_MAX_CLIENTS
            api_server_name = all_data["name"]
            api_max_peers = DEFAULT_XRAY_ORIGIN_MAX_CLIENTS
            protocol_name = "xray"
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
                server.extra_data = extra

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
                    protocol=texts.PROTOCOL_XRAY_ORIGIN,
                    max_clients=api_max_peers,
                    api_url=safe(all_data["api_url"]),
                ),
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

        if AMNEZIA_PROTOCOL not in protocols:
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

        if api_max_peers == server_info.SERVER_MAX_PEERS:
            logger.warning(
                "Amnezia API did not return max peers for %s. "
                "Using safe default %s instead of %s.",
                all_data["api_url"],
                SAFE_DEFAULT_MAX_PEERS,
                server_info.SERVER_MAX_PEERS,
            )

            api_max_peers = SAFE_DEFAULT_MAX_PEERS

        api_server_name = server_info.name or all_data["name"]

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

        server = await create_server(
            session,
            name=api_server_name,
            country_flag=all_data["country_flag"],
            api_url=all_data["api_url"],
            api_key=api_key,
            protocol=AMNEZIA_PROTOCOL,
            max_clients=api_max_peers,
        )

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
                protocol=AMNEZIA_PROTOCOL,
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
