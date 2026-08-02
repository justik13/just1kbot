#!/usr/bin/env python3
"""Apply the reviewed payment checkout UX fix.

This temporary codemod is count-checked and removed by the publishing workflow.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: str, old: str, new: str, *, expected: int = 1) -> None:
    file_path = ROOT / path
    content = file_path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != expected:
        raise RuntimeError(
            f"{path}: expected {expected} occurrence(s), found {count}: {old!r}"
        )
    file_path.write_text(content.replace(old, new), encoding="utf-8")


def fix_keyboard() -> None:
    replace_exact(
        "bot/keyboards/payment.py",
        '        text="❌ Отменить",\n',
        '        text="← Вернуться позже",\n',
    )


def fix_handler() -> None:
    path = "bot/handlers/payment/yookassa_routes.py"
    replace_exact(path, "import logging\n", "import asyncio\nimport logging\n")
    replace_exact(
        path,
        "from database.connection import queue_post_commit_task\n",
        "from database.connection import queue_post_commit_task, session_scope\n",
    )
    replace_exact(
        path,
        "logger = logging.getLogger(__name__)\n\n_PAYMENT_ERROR_MESSAGES = {\n",
        "logger = logging.getLogger(__name__)\n\n"
        "_PAYMENT_LINK_POLL_SECONDS = 0.5\n"
        "_PAYMENT_LINK_WAIT_SECONDS = 20.0\n\n"
        "_PAYMENT_ERROR_MESSAGES = {\n",
    )
    replace_exact(
        path,
        "\n\nasync def _create_and_show_payment(\n",
        "\n\nasync def _wait_and_show_payment_url(\n"
        "    bot,\n"
        "    chat_id: int,\n"
        "    payment_id: int,\n"
        "    tariff_id: int,\n"
        "    tariff_price: int,\n"
        "    source: str,\n"
        ") -> None:\n"
        "    loop = asyncio.get_running_loop()\n"
        "    deadline = loop.time() + _PAYMENT_LINK_WAIT_SECONDS\n\n"
        "    while loop.time() < deadline:\n"
        "        async with session_scope() as poll_session:\n"
        "            payment = await get_payment_by_id_simple(poll_session, payment_id)\n\n"
        "        if payment is None:\n"
        "            return\n"
        "        if payment.payment_url and payment.checkout_status == \"active\":\n"
        "            await _send_payment_url_to_user(\n"
        "                bot,\n"
        "                chat_id,\n"
        "                payment.payment_url,\n"
        "                payment.id,\n"
        "                tariff_id,\n"
        "                tariff_price,\n"
        "                source,\n"
        "            )\n"
        "            return\n"
        "        if (\n"
        "            payment.checkout_status == \"abandoned\"\n"
        "            or payment.provider_status\n"
        "            in {\"succeeded\", \"canceled\", \"refunded\", \"manual_review\"}\n"
        "        ):\n"
        "            return\n"
        "        await asyncio.sleep(_PAYMENT_LINK_POLL_SECONDS)\n\n\n"
        "async def _create_and_show_payment(\n",
    )
    replace_exact(
        path,
        "        await render_hub(\n"
        "            target.bot,\n"
        "            target.chat.id,\n"
        "            \"⏳ Создаём ссылку на оплату.\\nНажмите «Обновить», чтобы проверить готовность.\",\n"
        "            builder.as_markup(),\n"
        "        )\n"
        "        return\n",
        "        await render_hub(\n"
        "            target.bot,\n"
        "            target.chat.id,\n"
        "            (\n"
        "                \"⏳ Создаём ссылку на оплату.\\n\"\n"
        "                \"Обычно это занимает несколько секунд — \"\n"
        "                \"страница обновится автоматически.\"\n"
        "            ),\n"
        "            builder.as_markup(),\n"
        "        )\n"
        "        queue_post_commit_task(\n"
        "            session,\n"
        "            lambda b=target.bot, cid=target.chat.id, pid=payment.id, tid=tariff.id, tp=tariff.price_rub, s=source: (\n"
        "                _wait_and_show_payment_url(b, cid, pid, tid, tp, s)\n"
        "            ),\n"
        "        )\n"
        "        return\n",
    )
    replace_exact(
        path,
        "    try:\n"
        "        queued = await PaymentService.cancel_payment_via_api(session, payment_id)\n"
        "    except (OperationalError, OSError, TimeoutError) as e:\n"
        "        logger.warning(\"Temporary error cancelling payment %s: %s\", payment_id, e)\n"
        "        await callback.answer(\"⚠️ Временная ошибка. Попробуйте позже.\", show_alert=True)\n"
        "        return\n"
        "    except Exception as e:\n"
        "        logger.warning(\"Failed to queue cancellation %s: %s\", payment_id, e)\n"
        "        queued = False\n"
        "    await state.clear()\n"
        "    await callback.answer(\n"
        "        \"Запрос на отмену поставлен в очередь\"\n"
        "        if queued\n"
        "        else texts.PAYMENT_ALREADY_PROCESSED,\n"
        "        show_alert=not queued,\n"
        "    )\n",
        "    if payment.provider_status == \"pending\" and payment.external_id:\n"
        "        await state.clear()\n"
        "        await callback.answer(\n"
        "            (\n"
        "                \"Платёж сохранён. Вы сможете вернуться к нему \"\n"
        "                \"через раздел оплаты.\"\n"
        "            ),\n"
        "            show_alert=False,\n"
        "        )\n"
        "    else:\n"
        "        try:\n"
        "            queued = await PaymentService.cancel_payment_via_api(\n"
        "                session, payment_id\n"
        "            )\n"
        "        except (OperationalError, OSError, TimeoutError) as e:\n"
        "            logger.warning(\n"
        "                \"Temporary error cancelling payment %s: %s\", payment_id, e\n"
        "            )\n"
        "            await callback.answer(\n"
        "                \"⚠️ Временная ошибка. Попробуйте позже.\", show_alert=True\n"
        "            )\n"
        "            return\n"
        "        except Exception as e:\n"
        "            logger.warning(\"Failed to queue cancellation %s: %s\", payment_id, e)\n"
        "            queued = False\n"
        "        await state.clear()\n"
        "        await callback.answer(\n"
        "            \"Запрос на отмену поставлен в очередь\"\n"
        "            if queued\n"
        "            else texts.PAYMENT_ALREADY_PROCESSED,\n"
        "            show_alert=not queued,\n"
        "        )\n",
    )


def fix_reusable_checkout_validation() -> None:
    path = ROOT / "services/checkout_conflicts.py"
    content = path.read_text(encoding="utf-8")
    marker = "async def is_valid_reusable_purchase_intent(\n"
    if content.count(marker) != 1:
        raise RuntimeError("checkout_conflicts.py: reusable intent marker mismatch")
    prefix = content.split(marker, 1)[0]
    replacement = '''async def is_valid_reusable_purchase_intent(
    session, conflict: FinancialCheckoutConflict, *, user_id: int, tariff_id: int
) -> bool:
    """Validate an in-progress or provider-created purchase intent for reuse.

    A ready provider payment remains the only safe checkout to show again: creating
    a second payment would leave two independently payable links.  The narrowly
    defined abandoned state below exists only to recover rows produced by the old
    misleading user-cancel button.
    """
    payment = conflict.payment
    if (
        conflict.operation_type not in {"purchase", "renew"}
        or payment.user_id != user_id
    ):
        return False
    quote = (
        await session.get(TariffQuote, payment.tariff_quote_id)
        if payment.tariff_quote_id
        else None
    )
    version = (
        await session.get(TariffVersion, payment.tariff_version_id)
        if payment.tariff_version_id
        else None
    )
    if (
        not quote
        or not version
        or quote.payment_id != payment.id
        or quote.user_id != user_id
    ):
        return False
    if (
        quote.operation_type != conflict.operation_type
        or quote.target_tariff_version_id != version.id
        or version.tariff_id != tariff_id
        or payment.tariff_id != tariff_id
        or payment.amount != quote.confirmed_payment_required_rub
        or payment.amount != version.price_rub
        or payment.snapshot_amount != payment.amount
        or payment.currency != quote.currency
        or payment.snapshot_currency != payment.currency
        or payment.currency != version.currency
        or payment.snapshot_duration_days != version.duration_hours // 24
        or payment.snapshot_device_limit != version.device_limit
        or not payment.public_order_id
        or not payment.provider_idempotency_key
        or not payment.provider_required
        or payment.fulfillment_status != "not_ready"
        or payment.reconciliation_status in {"mismatch", "manual_review"}
        or quote.manual_review_at is not None
    ):
        return False
    operations = list(
        (
            await session.scalars(
                select(PaymentProviderOperation).where(
                    PaymentProviderOperation.payment_id == payment.id,
                    PaymentProviderOperation.operation_type == "create_payment",
                )
            )
        ).all()
    )
    if (
        len(operations) != 1
        or operations[0].idempotency_key != payment.provider_idempotency_key
    ):
        return False
    operation = operations[0]
    payload = operation.payload
    payload_valid = (
        isinstance(payload, dict)
        and set(payload)
        == {"amount", "description", "confirmation", "metadata", "capture"}
        and payload.get("amount")
        == {"value": format(payment.amount, ".2f"), "currency": payment.currency}
        and payload.get("capture") is True
        and payload.get("metadata")
        == {"order_id": payment.public_order_id, "local_payment_id": str(payment.id)}
        and isinstance(payload.get("description"), str)
        and bool(payload["description"])
        and isinstance(payload.get("confirmation"), dict)
        and set(payload["confirmation"]) == {"type", "return_url"}
        and payload["confirmation"].get("type") == "redirect"
        and bool(payload["confirmation"].get("return_url"))
    )
    if not payload_valid:
        return False

    operation_available = operation.status == "succeeded" or (
        operation.status in RUNNABLE
        and operation.attempts < operation.max_attempts
        and now_utc() - operation.created_at < timedelta(hours=24)
    )
    if not operation_available:
        return False

    creating_intent = (
        quote.status == "active"
        and quote.diagnostic_reason is None
        and quote.expires_at > now_utc()
        and payment.checkout_status == "active"
        and payment.provider_status == "creating"
        and operation.status in RUNNABLE
        and payment.external_id is None
        and payment.payment_url is None
    )
    ready_provider_payment = (
        quote.status == "active"
        and quote.diagnostic_reason is None
        and quote.expires_at > now_utc()
        and payment.checkout_status == "active"
        and payment.provider_status in {"pending", "waiting_for_capture"}
        and bool(payment.external_id)
        and bool(payment.payment_url)
    )
    recoverable_old_user_cancel = (
        quote.status == "cancelled"
        and quote.diagnostic_reason == "checkout_abandoned_by_user"
        and quote.expires_at > now_utc()
        and payment.checkout_status == "abandoned"
        and payment.user_cancel_requested_at is not None
        and payment.provider_status in {"pending", "waiting_for_capture"}
        and bool(payment.external_id)
    )
    return creating_intent or ready_provider_payment or recoverable_old_user_cancel
'''
    path.write_text(prefix + replacement, encoding="utf-8")


def fix_payment_service() -> None:
    path = "services/payment_service/service.py"
    replace_exact(
        path,
        "        from services.payment_provider_operations import enqueue_create\n",
        "        from services.payment_provider_operations import (\n"
        "            enqueue_create,\n"
        "            ensure_reconcile_payment_operation,\n"
        "        )\n",
    )
    replace_exact(
        path,
        "        if conflicts:\n"
        "            if len(conflicts) == 1 and await is_valid_reusable_purchase_intent(\n"
        "                session, conflicts[0], user_id=user_id, tariff_id=tariff_id\n"
        "            ):\n"
        "                return conflicts[0].payment, conflicts[0].payment.payment_url\n"
        "            return None, \"unfinished_checkout_exists\"\n",
        "        if conflicts:\n"
        "            if len(conflicts) == 1 and await is_valid_reusable_purchase_intent(\n"
        "                session, conflicts[0], user_id=user_id, tariff_id=tariff_id\n"
        "            ):\n"
        "                existing = conflicts[0].payment\n"
        "                if existing.checkout_status == \"abandoned\":\n"
        "                    quote = await session.scalar(\n"
        "                        select(TariffQuote)\n"
        "                        .where(TariffQuote.id == existing.tariff_quote_id)\n"
        "                        .with_for_update()\n"
        "                    )\n"
        "                    if (\n"
        "                        not quote\n"
        "                        or quote.status != \"cancelled\"\n"
        "                        or quote.diagnostic_reason\n"
        "                        != \"checkout_abandoned_by_user\"\n"
        "                        or quote.expires_at <= now_utc()\n"
        "                    ):\n"
        "                        return None, \"unfinished_checkout_exists\"\n"
        "                    existing.checkout_status = \"active\"\n"
        "                    existing.user_cancel_requested_at = None\n"
        "                    quote.status = \"active\"\n"
        "                    quote.diagnostic_reason = None\n"
        "                    if existing.external_id and not existing.payment_url:\n"
        "                        await ensure_reconcile_payment_operation(\n"
        "                            session,\n"
        "                            existing,\n"
        "                            reason=\"resume_user_abandoned_checkout\",\n"
        "                        )\n"
        "                    await session.flush()\n"
        "                return existing, existing.payment_url\n"
        "            return None, \"unfinished_checkout_exists\"\n",
    )


def fix_provider_reconciliation() -> None:
    replace_exact(
        "services/payment_provider_operations.py",
        "        if claim.operation_type == \"create_payment\":\n"
        "            confirmation = data.get(\"confirmation\") or {}\n"
        "            url = confirmation.get(\"confirmation_url\") or confirmation.get(\"url\")\n"
        "            if data.get(\"id\"):\n"
        "                payment.external_id = str(data[\"id\"])\n"
        "                payment.payment_url = url or payment.payment_url\n"
        "                payment.payment_method = \"yookassa\"\n",
        "        confirmation = data.get(\"confirmation\") or {}\n"
        "        url = confirmation.get(\"confirmation_url\") or confirmation.get(\"url\")\n"
        "        if (\n"
        "            url\n"
        "            and status in {\"pending\", \"waiting_for_capture\"}\n"
        "            and claim.operation_type in {\"create_payment\", \"reconcile_payment\"}\n"
        "        ):\n"
        "            payment.payment_url = url\n"
        "        if claim.operation_type == \"create_payment\" and data.get(\"id\"):\n"
        "            payment.external_id = str(data[\"id\"])\n"
        "            payment.payment_method = \"yookassa\"\n",
    )


def main() -> None:
    fix_keyboard()
    fix_handler()
    fix_reusable_checkout_validation()
    fix_payment_service()
    fix_provider_reconciliation()


if __name__ == "__main__":
    main()
