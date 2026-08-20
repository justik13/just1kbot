import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button, get_device_keyboard
from bot.states import DeviceManagementStates
from database.models import User, VPNProfile
from database.repositories.profiles_repo import (
    get_profile_by_id,
    get_user_profiles,
    update_profile,
)
from database.repositories.servers_repo import get_server_by_id
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.telegram import render_hub, safe

from .common import DEVICE_NAME_REGEX

router = Router()


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    profile_id = parse_callback_id(callback.data, 1)

    if profile_id is None:
        await callback.answer(texts.UI_BOT_HANDLERS_CONNECTION_DEVICE_RENAME_ROUTES_L36_1, show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    if profile.provisioning_status in ("deleting", "create_cleanup_pending", "pending_create"):
        if profile.provisioning_status == "deleting":
            msg = "🗑 Устройство уже удаляется с сервера."
        elif profile.provisioning_status == "pending_create":
            msg = texts.DEVICE_CREATE_IN_PROGRESS
        else:
            msg = "⚠️ Идёт автоматическое восстановление после сбоя. Попробуйте позже."
        await callback.answer(msg, show_alert=True)
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await callback.answer(
            texts.DEVICE_ACCESS_INACTIVE,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.update_data(profile_id=profile_id)
    await state.set_state(DeviceManagementStates.rename_device)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_RENAME_PROMPT,
        get_back_button(f"manage_device:{profile_id}"),
    )


@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    try:
        await message.delete()
    except Exception:
        pass

    if not message.text or message.text.startswith("/"):
        await state.clear()
        return

    data = await state.get_data()
    profile_id = data.get("profile_id")
    if not profile_id or not db_user:
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_ACCESS_DENIED,
            get_back_button("back_to_connections"),
        )
        return

    profile = (
        await session.execute(
            select(VPNProfile)
            .where(
                VPNProfile.id == profile_id,
                VPNProfile.user_id == db_user.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if not profile:
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_ACCESS_DENIED,
            get_back_button("back_to_connections"),
        )
        return

    if profile.provisioning_status in ("deleting", "create_cleanup_pending", "pending_create"):
        await state.clear()
        if profile.provisioning_status == "deleting":
            msg = "🗑 Устройство уже удаляется с сервера."
        elif profile.provisioning_status == "pending_create":
            msg = texts.DEVICE_CREATE_IN_PROGRESS
        else:
            msg = "⚠️ Идёт автоматическое восстановление после сбоя. Попробуйте позже."
        await render_hub(
            message.bot,
            message.chat.id,
            msg,
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await state.clear()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.DEVICE_ACCESS_INACTIVE,
            get_back_button("back_to_connections"),
        )
        return

    base_new_name = message.text.strip()

    if not base_new_name:
        await render_hub(
            message.bot,
            message.chat.id,
            "⚠️ Имя устройства не может быть пустым.\n\nПожалуйста, введите имя устройства (от 1 до 16 символов):",
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    if len(base_new_name) > 16:
        await render_hub(
            message.bot,
            message.chat.id,
            f"⚠️ Имя слишком длинное ({len(base_new_name)} из 16 символов).\n\nПожалуйста, введите имя покороче (максимум 16 символов):",
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    if not DEVICE_NAME_REGEX.match(base_new_name):
        await render_hub(
            message.bot,
            message.chat.id,
            "⚠️ Имя содержит недопустимые символы.\n\nРазрешены только буквы, цифры, пробелы, дефисы, подчёркивания и знак #.\n\nПопробуйте ещё раз:",
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    if re.search(r'#\d+$', base_new_name):
        new_name = base_new_name
    else:
        m = re.search(r'#(\d+)$', profile.device_name)
        slot_suffix = f" #{m.group(1)}" if m else ""
        new_name = f"{base_new_name}{slot_suffix}"

    existing_profiles = await get_user_profiles(session, db_user.id)

    for p in existing_profiles:
        if (
            p.id != profile.id
            and p.server_id == profile.server_id
            and p.device_name.lower() == new_name.lower()
        ):
            await render_hub(
                message.bot,
                message.chat.id,
                f"⚠️ Устройство с именем «<b>{safe(new_name)}</b>» уже существует на этой локации.\n\nПожалуйста, введите другое имя:",
                get_back_button(f"manage_device:{profile.id}"),
            )
            return

    old_name = profile.device_name
    await update_profile(
        session,
        profile,
        device_name=new_name,
    )

    from services.audit_service import AuditService
    await AuditService.log_action(
        session,
        admin_id=0,
        action="DEVICE_RENAME",
        target_type="user",
        target_id=db_user.id,
        details={
            "old_name": old_name,
            "new_name": new_name,
            "profile_id": profile.id,
        },
    )

    server = await get_server_by_id(session, profile.server_id)
    from config.settings import get_settings
    from services.amnezia_bridge_token_service import AmneziaBridgeTokenService

    from .device_view_routes import (
        can_show_amnezia_bridge,
        can_show_config_actions,
        can_show_delete_action,
    )

    config_ready = can_show_config_actions(profile)
    show_delete = can_show_delete_action(profile)
    amnezia_bridge_url = None
    if can_show_amnezia_bridge(profile, server):
        settings = get_settings()
        amnezia_bridge_url = AmneziaBridgeTokenService.build_bridge_url(
            domain=settings.DOMAIN,
            profile_id=profile.id,
            user_id=db_user.id,
        )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.DEVICE_RENAMED_SUCCESS.format(
            device_name=safe(new_name),
        ),
        get_device_keyboard(
            profile.id,
            config_ready=config_ready,
            show_delete=show_delete,
            amnezia_bridge_url=amnezia_bridge_url,
        ),
    )

    await state.clear()