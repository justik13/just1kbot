import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import DeviceManagementStates
from database.models import User, VPNProfile
from database.repositories.profiles_repo import (
    get_profile_by_id,
    get_user_profiles,
    update_profile,
)
from services.subscription import SubscriptionService
from utils.callbacks import parse_callback_id
from utils.telegram import EFFECT_LIKE, render_hub, safe

from .common import DEVICE_NAME_REGEX
from .device_view_routes import render_device_screen

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
        await callback.answer(texts.ERROR_INVALID_REQUEST, show_alert=True)
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
            msg = texts.DEVICE_DELETE_ALREADY_IN_PROGRESS
        elif profile.provisioning_status == "pending_create":
            msg = texts.DEVICE_CREATE_IN_PROGRESS
        else:
            msg = texts.DEVICE_SELF_HEALING_IN_PROGRESS
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
        get_back_button(f"manage_device:{profile_id}", text=texts.BTN_PAYMENT_CANCEL),
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
            msg = texts.DEVICE_DELETE_ALREADY_IN_PROGRESS
        elif profile.provisioning_status == "pending_create":
            msg = texts.DEVICE_CREATE_IN_PROGRESS
        else:
            msg = texts.DEVICE_SELF_HEALING_IN_PROGRESS
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

    raw_text = message.text.strip()

    # Extract the permanent slot number assigned to this device
    m = re.search(r'#(\d+)$', profile.device_name)
    slot_num = m.group(1) if m else str(profile.id)

    # Strip any user-typed trailing #... to get the clean base name
    cleaned_base = re.sub(r'\s*#\d+$', '', raw_text).strip()
    if not cleaned_base:
        err = texts.CONNECTION_ACTIONS_DEVICE_RENAME_IMYA_NE_MOZHET_BYT_PUSTYM_VVED
        await render_hub(
            message.bot,
            message.chat.id,
            f"{err}\n\n{texts.DEVICE_RENAME_PROMPT}",
            get_back_button(f"manage_device:{profile.id}", text=texts.BTN_PAYMENT_CANCEL),
        )
        return

    if len(cleaned_base) > 16:
        err = texts.CONNECTION_ACTIONS_DEVICE_RENAME_IMYA_SLISHKOM_DLINNOE_IZ_16_SI.format(len_cleaned_base=len(cleaned_base))
        await render_hub(
            message.bot,
            message.chat.id,
            f"{err}\n\n{texts.DEVICE_RENAME_PROMPT}",
            get_back_button(f"manage_device:{profile.id}", text=texts.BTN_PAYMENT_CANCEL),
        )
        return

    if not DEVICE_NAME_REGEX.match(cleaned_base):
        err = texts.CONNECTION_ACTIONS_DEVICE_RENAME_IMYA_SODERZHIT_NEDOPUSTIMYE_SI
        await render_hub(
            message.bot,
            message.chat.id,
            f"{err}\n\n{texts.DEVICE_RENAME_PROMPT}",
            get_back_button(f"manage_device:{profile.id}", text=texts.BTN_PAYMENT_CANCEL),
        )
        return

    new_name = f"{cleaned_base} #{slot_num}"

    existing_profiles = await get_user_profiles(session, db_user.id, include_deleting=True)

    for p in existing_profiles:
        if (
            p.id != profile.id
            and p.server_id == profile.server_id
            and p.device_name.lower() == new_name.lower()
        ):
            err = texts.CONNECTION_ACTIONS_DEVICE_RENAME_DEVICE_S_IMENEM_UZHE_SUSHC.format(safe_new_name=safe(new_name))
            await render_hub(
                message.bot,
                message.chat.id,
                f"{err}\n\n{texts.DEVICE_RENAME_PROMPT}",
                get_back_button(f"manage_device:{profile.id}", text=texts.BTN_PAYMENT_CANCEL),
            )
            return

    old_name = profile.device_name
    from sqlalchemy.exc import IntegrityError
    try:
        nested_ctx = getattr(session, "begin_nested", None)
        if callable(nested_ctx):
            ctx = nested_ctx()
            if hasattr(ctx, "__aenter__"):
                async with ctx:
                    await update_profile(
                        session,
                        profile,
                        device_name=new_name,
                    )
            else:
                await update_profile(
                    session,
                    profile,
                    device_name=new_name,
                )
        else:
            await update_profile(
                session,
                profile,
                device_name=new_name,
            )
    except IntegrityError:
        err = texts.CONNECTION_ACTIONS_DEVICE_RENAME_DEVICE_S_IMENEM_UZHE_SUSHC.format(safe_new_name=safe(new_name))
        await render_hub(
            message.bot,
            message.chat.id,
            f"{err}\n\n{texts.DEVICE_RENAME_PROMPT}",
            get_back_button(f"manage_device:{profile.id}", text=texts.BTN_PAYMENT_CANCEL),
        )
        return

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

    await render_device_screen(
        message.bot,
        message.chat.id,
        profile,
        db_user,
        session,
        message_effect_id=EFFECT_LIKE,
        notice=texts.DEVICE_RENAMED_SUCCESS.format(
            device_name=safe(new_name),
        ),
    )

    await state.clear()
