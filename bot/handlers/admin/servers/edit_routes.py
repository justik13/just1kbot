import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.models import APIOperation, VPNProfile
from database.repositories.servers_repo import (
    get_server_by_api_url,
    get_server_by_id,
    update_server,
)
from services.amnezia_client import (
    AmneziaClient,
    cleanup_server_circuit_breakers,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.callbacks import parse_callback_id
from utils.security import is_safe_url
from utils.telegram import render_hub, safe

from .common import URL_REGEX, normalize_api_url

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_server_edit_name:"))
async def start_edit_server_name(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L50_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await state.update_data(
        server_id=server_id,
        edit_field="name",
    )

    await state.set_state(AdminStates.editing_server)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_RENAME_PROMPT,
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server)
async def process_edit_server_name(
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
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    new_name = message.text.strip()

    if len(new_name) > 255:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NAME_TOO_LONG.format(max=255),
            get_back_button("admin_servers"),
        )
        return

    await update_server(
        session,
        server,
        name=new_name,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"name -> {new_name}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_RENAMED.format(
            name=safe(new_name),
        ),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"name to {new_name}"
    )

    await state.clear()


@router.callback_query(F.data.startswith("admin_server_edit_flag:"))
async def start_edit_server_flag(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L182_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    current_flag = server.country_flag or texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L199_1

    await state.update_data(
        server_id=server_id,
        edit_field="flag",
    )

    await state.set_state(AdminStates.editing_server_flag)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_FLAG_PROMPT_EDIT.format(
            current_flag=safe(current_flag),
        ),
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server_flag)
async def process_edit_server_flag(
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
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    new_flag = message.text.strip()

    if len(new_flag) > 10:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_FLAG_TOO_LONG,
            get_back_button("admin_servers"),
        )
        return

    await update_server(
        session,
        server,
        country_flag=new_flag,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"flag -> {new_flag}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_FLAG_UPDATED.format(flag=safe(new_flag)),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"flag to {new_flag}"
    )

    await state.clear()


@router.callback_query(F.data.startswith("admin_server_edit_url:"))
async def start_edit_server_url(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L324_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await state.update_data(server_id=server_id)
    await state.set_state(AdminStates.editing_server_url)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_EDIT_URL_PROMPT,
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server_url)
async def process_edit_server_url(
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
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    new_url = normalize_api_url(message.text)

    if len(new_url) > 500:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_URL_TOO_LONG.format(max=500),
            get_back_button("admin_servers"),
        )
        return

    if not URL_REGEX.match(new_url):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_INVALID_URL,
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )
        return

    if not await is_safe_url(new_url):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_INVALID_URL,
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )
        return

    existing = await get_server_by_api_url(session, new_url)

    if existing and existing.id != server_id:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_DUPLICATE_URL.format(
                api_url=safe(new_url),
            ),
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )

        await state.clear()

        return

    client = AmneziaClient(new_url, server.api_key)

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

    if "amneziawg2" not in server_info.protocols:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_PROTOCOL_NOT_SUPPORTED.format(
                protocols=safe(
                    ", ".join(server_info.protocols)
                    if server_info.protocols
                    else texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L476_1
                ),
            ),
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )
        await state.clear()
        return

    if server.api_url != new_url:
        profiles_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.server_id == server_id,
                    or_(
                        VPNProfile.peer_id.is_not(None),
                        VPNProfile.provisioning_status.in_((
                            "pending_create",
                            "pending_update",
                            "deleting",
                            "create_cleanup_pending",
                            "create_failed",
                            "update_failed",
                            "delete_failed",
                            "active",
                        )),
                    ),
                )
            )
        ).scalar_one()

        active_ops_count = (
            await session.execute(
                select(func.count(APIOperation.id)).where(
                    APIOperation.server_id == server_id,
                    APIOperation.status.in_(("pending", "processing", "retry")),
                )
            )
        ).scalar_one()

        if profiles_count > 0 or active_ops_count > 0:
            await render_hub(
                message.bot,
                message.chat.id,
                f"❌ Нельзя изменить адрес сервера, пока на нём есть устройства или активные операции.\n\n"
                f"• Связанных устройств: {profiles_count}\n"
                f"• Операций в обработке: {active_ops_count}\n\n"
                "Для подключения нового узла добавьте новый сервер в панели управления.",
                get_back_button(f"admin_server_card:{server_id}"),
                parse_mode="HTML",
            )
            await state.clear()
            return

    old_url = server.api_url

    await update_server(session, server, api_url=new_url)

    cleanup_server_circuit_breakers(old_url)

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"api_url -> {new_url}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_URL_UPDATED.format(
            api_url=safe(new_url),
        ),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"api_url to {new_url}"
    )

    await state.clear()


@router.callback_query(F.data.startswith("admin_server_edit_key:"))
async def start_edit_server_key(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L537_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await state.update_data(server_id=server_id)
    await state.set_state(AdminStates.editing_server_key)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_EDIT_KEY_PROMPT,
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server_key)
async def process_edit_server_key(
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
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    new_key = message.text.strip()
    try:
        await message.delete()
    except Exception as e:
        logger.warning("Failed to delete secret message %s: %s", message.message_id, e)


    if not new_key or len(new_key) < 8:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_API_KEY_SHORT.format(min=8),
            get_back_button("admin_servers"),
        )
        return


    client = AmneziaClient(server.api_url, new_key)

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

    if "amneziawg2" not in server_info.protocols:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_PROTOCOL_NOT_SUPPORTED.format(
                protocols=safe(
                    ", ".join(server_info.protocols)
                    if server_info.protocols
                    else texts.RUNTIME_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L476_1
                ),
            ),
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )

        await state.clear()

        return

    if server.api_key != new_key:
        profiles_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.server_id == server_id,
                    or_(
                        VPNProfile.peer_id.is_not(None),
                        VPNProfile.provisioning_status.in_((
                            "pending_create",
                            "pending_update",
                            "deleting",
                            "create_cleanup_pending",
                            "create_failed",
                            "update_failed",
                            "delete_failed",
                            "active",
                        )),
                    ),
                )
            )
        ).scalar_one()

        active_ops_count = (
            await session.execute(
                select(func.count(APIOperation.id)).where(
                    APIOperation.server_id == server_id,
                    APIOperation.status.in_(("pending", "processing", "retry")),
                )
            )
        ).scalar_one()

        if profiles_count > 0 or active_ops_count > 0:
            await render_hub(
                message.bot,
                message.chat.id,
                f"❌ Нельзя изменить ключ API сервера, пока на нём есть устройства или активные операции.\n\n"
                f"• Связанных устройств: {profiles_count}\n"
                f"• Операций в обработке: {active_ops_count}\n\n"
                "Для подключения нового узла добавьте новый сервер в панели управления.",
                get_back_button(f"admin_server_card:{server_id}"),
                parse_mode="HTML",
            )
            await state.clear()
            return

    await update_server(session, server, api_key=new_key)

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        "api_key -> [REDACTED]",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_KEY_UPDATED,
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"api_key"
    )

    await state.clear()


@router.callback_query(F.data.startswith("admin_server_edit_max_clients:"))
async def start_edit_server_max_clients(
    callback: CallbackQuery,
    state: FSMContext,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server_id = parse_callback_id(callback.data, 1)

    if server_id is None:
        await callback.answer(
            texts.UI_BOT_HANDLERS_ADMIN_SERVERS_EDIT_ROUTES_L707_1,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await state.update_data(server_id=server_id)
    await state.set_state(AdminStates.editing_server_max_clients)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT,
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server_max_clients)
async def process_edit_server_max_clients(
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
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    try:
        new_value = int(message.text.strip())

        if new_value < 1:
            raise ValueError
    except ValueError:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NUMBER_GT_ZERO,
            get_back_button("admin_servers"),
        )
        return

    profiles_count = await session.scalar(
        select(func.count(VPNProfile.id)).where(
            VPNProfile.server_id == server_id,
        )
    ) or 0

    if profiles_count > new_value:
        warning = texts.ADMIN_SERVER_MAX_CLIENTS_WARNING.format(
            current=profiles_count,
            new=new_value,
        )

        await render_hub(
            message.bot,
            message.chat.id,
            warning,
            get_back_button(
                f"admin_server_card:{server_id}"
            ),
        )

    await update_server(session, server, max_clients=new_value)

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"max_clients: {server.max_clients} -> {new_value}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_MAX_CLIENTS_UPDATED.format(
            max_clients=new_value,
        ),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"max_clients to {new_value}"
    )

    await state.clear()