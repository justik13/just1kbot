"""Shared request parsing, error mapping, and post-commit background helpers for Mini App admin handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypeVar

from aiogram.exceptions import TelegramForbiddenError
from aiohttp import web
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import WhiteInternetProvisioningStatus, WhiteInternetStatus
from database.connection import queue_post_commit_task, session_scope
from database.models import User, UserDevice, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
from services.admin_nodes_service import AdminNodeServiceError
from services.white_internet_service import WhiteInternetService
from utils.telegram import safe_send_message

SchemaT = TypeVar("SchemaT", bound=BaseModel)
logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[object]] = set()
MAX_INT32 = 2_147_483_647
MAX_INT64 = 9_223_372_036_854_775_807


async def cancel_admin_background_tasks() -> None:
    """Cancel and drain in-flight admin Mini App background tasks on shutdown."""
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.clear()


def parse_positive_int(
    request: web.Request,
    name: str,
    *,
    label: str | None = None,
    max_value: int = MAX_INT32,
) -> tuple[int | None, web.Response | None]:
    """Parse a positive integer path parameter within [1, max_value]."""
    raw_id = request.match_info.get(name, "")
    err_label = label or name
    if not raw_id.isdigit():
        return None, web.json_response({"ok": False, "error": f"Invalid {err_label}"}, status=400)
    val = int(raw_id)
    if val < 1 or val > max_value:
        return None, web.json_response({"ok": False, "error": f"Invalid {err_label}"}, status=400)
    return val, None


def parse_bounded_int_query(
    request: web.Request,
    name: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    """Parse and clamp an integer query parameter."""
    raw = (request.query.get(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return min(max_value, max(min_value, val))


async def parse_json_model(
    request: web.Request,
    model_cls: type[SchemaT],
) -> tuple[SchemaT | None, web.Response | None]:
    """Read request JSON object and validate it against a Pydantic schema."""
    try:
        raw_body = await request.json()
    except Exception:
        return None, web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

    if not isinstance(raw_body, dict):
        return None, web.json_response({"ok": False, "error": "JSON object expected"}, status=400)

    try:
        return model_cls.model_validate(raw_body), None
    except ValidationError as exc:
        first_err = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first_err.get("loc", ()))
        msg = first_err.get("msg", "Validation error")
        detail = f"{loc}: {msg}" if loc else msg
        return None, web.json_response({"ok": False, "error": detail}, status=400)


def service_error_response(exc: AdminNodeServiceError) -> web.Response:
    """Convert an AdminNodeServiceError into a JSON error response."""
    return web.json_response({"ok": False, "error": exc.message}, status=exc.status_code)


def safe_admin_error_response(
    exc: Exception,
    *,
    status: int = 400,
    default_msg: str = "Не удалось выполнить операцию",
) -> web.Response:
    """Return safe error JSON exposing only domain/validation errors."""
    if isinstance(exc, AdminNodeServiceError):
        return web.json_response({"ok": False, "error": exc.message}, status=exc.status_code)
    if isinstance(exc, (white_internet_repo.WhiteInternetError, ValueError, LookupError)):
        msg = str(exc).strip() or default_msg
        return web.json_response({"ok": False, "error": msg}, status=status)
    logger.warning("Unexpected admin handler error (%s): %s", type(exc).__name__, exc)
    return web.json_response({"ok": False, "error": default_msg}, status=status)


async def queue_wi_inline_sync(
    session: AsyncSession,
    wi_sub: WhiteInternetSubscription,
    reason: str,
) -> None:
    """Queue post-commit inline sync to WI hub nodes when provisioning state requires it."""
    if (
        wi_sub.status not in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING)
        or wi_sub.provisioning_status
        not in (
            WhiteInternetProvisioningStatus.PENDING_CREATE,
            WhiteInternetProvisioningStatus.PENDING_UPDATE,
        )
    ):
        return
    hubs = await WhiteInternetService.get_hub_nodes(session)
    target_hubs = [
        (h.id, h.api_url, h.api_key, h.xray_instance_epoch)
        for h in hubs
        if h.api_url and h.api_key
    ]
    if not target_hubs:
        return

    sub_id_snap = wi_sub.id
    ver_snap = wi_sub.desired_version

    async def _sync_wi_after_admin_op() -> None:
        async with session_scope() as bg_sess:
            bg_sub = await white_internet_repo.get_subscription_by_id(bg_sess, sub_id_snap)
            if (
                bg_sub is not None
                and bg_sub.status in (WhiteInternetStatus.ACTIVE, WhiteInternetStatus.PENDING)
                and bg_sub.provisioning_status
                in (
                    WhiteInternetProvisioningStatus.PENDING_CREATE,
                    WhiteInternetProvisioningStatus.PENDING_UPDATE,
                )
            ):
                await WhiteInternetService._try_inline_sync_multi(
                    bg_sess,
                    bg_sub,
                    target_hubs,
                    idempotency_key=f"admin_{reason}:{sub_id_snap}:{ver_snap}",
                )

    queue_post_commit_task(session, _sync_wi_after_admin_op)


async def queue_wi_revoke_uuids(
    session: AsyncSession,
    wi_sub: WhiteInternetSubscription,
    uuids: list[str] | tuple[str, ...],
    *,
    context: str,
) -> None:
    """Queue post-commit deprovisioning of specific UUIDs on all WI revocation ru_hubs."""
    clean_uuids = list(dict.fromkeys(str(u) for u in uuids if u))
    if not clean_uuids:
        return
    wi_hubs = await servers_repo.get_wi_revocation_ru_hubs(session)
    if not wi_hubs:
        return
    from services import white_internet_service

    version = wi_sub.desired_version
    for hub in wi_hubs:
        for rev_u in clean_uuids:
            white_internet_service._dispatch_deprovision(
                hub,
                rev_u,
                version,
                session=session,
                context=context,
            )


async def queue_wi_deprovision_all(
    session: AsyncSession,
    user_id: int,
    wi_sub: WhiteInternetSubscription,
    *,
    context: str,
) -> None:
    """Queue post-commit deprovisioning of subscription and all bound device UUIDs on WI hubs."""
    dev_rows = (
        await session.scalars(
            select(UserDevice.device_uuid).where(
                UserDevice.user_id == user_id,
                UserDevice.device_uuid.is_not(None),
            )
        )
    ).all()
    uuids_to_revoke = list(dict.fromkeys(str(u) for u in [wi_sub.uuid, *dev_rows] if u))
    if uuids_to_revoke:
        await white_internet_repo.add_pending_revocation_uuids(
            session, wi_sub.id, uuids_to_revoke
        )
    await queue_wi_revoke_uuids(session, wi_sub, uuids_to_revoke, context=context)


def queue_user_notification(
    session: AsyncSession,
    bot: Any,
    *,
    user_id: int,
    telegram_id: int,
    text: str,
) -> None:
    """Queue a post-commit Telegram DM using safe_send_message, marking is_bot_blocked on 403."""
    if bot is None or not text:
        return

    async def _send() -> None:
        try:
            await safe_send_message(bot, telegram_id, text, raise_forbidden=True)
        except TelegramForbiddenError:
            try:
                async with session_scope() as bg_sess:
                    await bg_sess.execute(
                        update(User).where(User.id == user_id).values(is_bot_blocked=True)
                    )
            except Exception:
                pass
        except Exception:
            pass

    queue_post_commit_task(session, _send)
