#!/usr/bin/env python3
"""Apply the reviewed immutable quote reissue fix for PR #51."""

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


def add_quote_reissue_repository_boundary() -> None:
    replace_exact(
        "database/repositories/tariff_quotes_repo.py",
        "from database.models import Tariff, TariffQuote, TariffVersion, User\n",
        "from database.models import Payment, Tariff, TariffQuote, TariffVersion, User\n",
    )
    path = ROOT / "database/repositories/tariff_quotes_repo.py"
    content = path.read_text(encoding="utf-8")
    marker = "    return quote, version\n"
    if content.count(marker) != 1:
        raise RuntimeError("tariff quote return marker mismatch")
    helper = '''\n\nasync def reissue_checkout_quote_for_existing_payment(\n    session: AsyncSession,\n    *,\n    quote: TariffQuote,\n    payment: Payment,\n    tariff: Tariff,\n) -> TariffQuote:\n    \"\"\"Create a fresh immutable quote around the same provider payment.\n\n    The previous quote is retained as history. Reissuing is allowed only for a\n    recent provider-backed purchase/renew payment whose complete frozen economics\n    still match the currently offered tariff.\n    \"\"\"\n    now = now_utc()\n    version = await session.get(TariffVersion, quote.target_tariff_version_id)\n    if (\n        quote.operation_type not in {\"purchase\", \"renew\"}\n        or payment.tariff_quote_id != quote.id\n        or quote.payment_id != payment.id\n        or payment.user_id != quote.user_id\n        or version is None\n        or payment.tariff_version_id != version.id\n        or payment.tariff_id != version.tariff_id\n        or tariff.id != version.tariff_id\n        or Decimal(tariff.price_rub) != version.price_rub\n        or tariff.duration_days * 24 != version.duration_hours\n        or tariff.device_limit != version.device_limit\n        or version.currency != \"RUB\"\n        or payment.amount != quote.confirmed_payment_required_rub\n        or payment.amount != version.price_rub\n        or payment.snapshot_amount != payment.amount\n        or payment.snapshot_currency != quote.currency\n        or payment.currency != quote.currency\n        or payment.snapshot_duration_days != version.duration_hours // 24\n        or payment.snapshot_device_limit != version.device_limit\n        or payment.external_id is None\n        or payment.provider_status not in {\"pending\", \"waiting_for_capture\"}\n        or now - payment.created_at >= timedelta(hours=24)\n    ):\n        raise CheckoutQuoteConflictError(\"existing_payment_snapshot_changed\")\n\n    if quote.status == \"active\":\n        if quote.expires_at > now:\n            return quote\n        quote.status = \"expired\"\n    elif quote.status == \"expired\":\n        pass\n    elif (\n        quote.status == \"cancelled\"\n        and quote.diagnostic_reason == \"checkout_abandoned_by_user\"\n        and payment.checkout_status == \"abandoned\"\n        and payment.user_cancel_requested_at is not None\n    ):\n        pass\n    else:\n        raise CheckoutQuoteConflictError(\"existing_quote_not_reissuable\")\n\n    # Keep the old cancelled/expired quote as immutable history, detach only its\n    # reciprocal pointer, and bind the payment to a newly issued active quote.\n    quote.payment_id = None\n    await session.flush()\n\n    reissued = TariffQuote(\n        public_id=uuid.uuid4(),\n        user_id=quote.user_id,\n        operation_type=quote.operation_type,\n        source_tariff_version_id=quote.source_tariff_version_id,\n        target_tariff_version_id=quote.target_tariff_version_id,\n        current_paid_hours=quote.current_paid_hours,\n        current_paid_value_rub=quote.current_paid_value_rub,\n        bonus_hours=quote.bonus_hours,\n        confirmed_payment_required_rub=quote.confirmed_payment_required_rub,\n        resulting_paid_hours=quote.resulting_paid_hours,\n        resulting_paid_value_rub=quote.resulting_paid_value_rub,\n        resulting_bonus_hours=quote.resulting_bonus_hours,\n        rounding_loss_hours=quote.rounding_loss_hours,\n        rounding_loss_value_rub=quote.rounding_loss_value_rub,\n        currency=quote.currency,\n        status=\"active\",\n        created_at=now,\n        expires_at=now + QUOTE_LIFETIME,\n        balance_as_of=quote.balance_as_of,\n        source_subscription_end=quote.source_subscription_end,\n        source_balance_fingerprint=quote.source_balance_fingerprint,\n        source_entitlement_entry_ids=quote.source_entitlement_entry_ids,\n        source_ledger_entry_ids=quote.source_ledger_entry_ids,\n    )\n    session.add(reissued)\n    await session.flush()\n\n    payment.tariff_quote_id = reissued.id\n    payment.checkout_status = \"active\"\n    payment.user_cancel_requested_at = None\n    reissued.payment_id = payment.id\n    await session.flush()\n    return reissued\n'''
    path.write_text(content.replace(marker, marker + helper), encoding="utf-8")


def adjust_reusable_checkout_validation() -> None:
    path = "services/checkout_conflicts.py"
    replace_exact(
        path,
        "    ready_provider_payment = (\n"
        "        quote.status == \"active\"\n"
        "        and quote.diagnostic_reason is None\n"
        "        and quote.expires_at > now_utc()\n"
        "        and payment.checkout_status == \"active\"\n"
        "        and payment.provider_status in {\"pending\", \"waiting_for_capture\"}\n"
        "        and bool(payment.external_id)\n"
        "        and bool(payment.payment_url)\n"
        "    )\n"
        "    recoverable_old_user_cancel = (\n"
        "        quote.status == \"cancelled\"\n"
        "        and quote.diagnostic_reason == \"checkout_abandoned_by_user\"\n"
        "        and quote.expires_at > now_utc()\n"
        "        and payment.checkout_status == \"abandoned\"\n"
        "        and payment.user_cancel_requested_at is not None\n"
        "        and payment.provider_status in {\"pending\", \"waiting_for_capture\"}\n"
        "        and bool(payment.external_id)\n"
        "    )\n",
        "    provider_payment_is_recent = (\n"
        "        now_utc() - payment.created_at < timedelta(hours=24)\n"
        "    )\n"
        "    ready_provider_payment = (\n"
        "        quote.status in {\"active\", \"expired\"}\n"
        "        and quote.diagnostic_reason is None\n"
        "        and payment.checkout_status == \"active\"\n"
        "        and payment.provider_status in {\"pending\", \"waiting_for_capture\"}\n"
        "        and bool(payment.external_id)\n"
        "        and bool(payment.payment_url)\n"
        "        and provider_payment_is_recent\n"
        "    )\n"
        "    recoverable_old_user_cancel = (\n"
        "        quote.status == \"cancelled\"\n"
        "        and quote.diagnostic_reason == \"checkout_abandoned_by_user\"\n"
        "        and payment.checkout_status == \"abandoned\"\n"
        "        and payment.user_cancel_requested_at is not None\n"
        "        and payment.provider_status in {\"pending\", \"waiting_for_capture\"}\n"
        "        and bool(payment.external_id)\n"
        "        and provider_payment_is_recent\n"
        "    )\n",
    )


def adjust_payment_service() -> None:
    replace_exact(
        "services/payment_service/service.py",
        "    get_or_create_checkout_quote,\n"
        "    lock_checkout_user,\n",
        "    get_or_create_checkout_quote,\n"
        "    lock_checkout_user,\n"
        "    reissue_checkout_quote_for_existing_payment,\n",
    )
    replace_exact(
        "services/payment_service/service.py",
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
        "                        or quote.diagnostic_reason != \"checkout_abandoned_by_user\"\n"
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
        "                return existing, existing.payment_url\n",
        "                existing = conflicts[0].payment\n"
        "                quote = await session.scalar(\n"
        "                    select(TariffQuote)\n"
        "                    .where(TariffQuote.id == existing.tariff_quote_id)\n"
        "                    .with_for_update()\n"
        "                )\n"
        "                if quote is None:\n"
        "                    return None, \"unfinished_checkout_exists\"\n"
        "                old_quote_id = quote.id\n"
        "                needs_reissue = (\n"
        "                    quote.status in {\"expired\", \"cancelled\"}\n"
        "                    or quote.expires_at <= now_utc()\n"
        "                )\n"
        "                if needs_reissue:\n"
        "                    try:\n"
        "                        quote = await reissue_checkout_quote_for_existing_payment(\n"
        "                            session,\n"
        "                            quote=quote,\n"
        "                            payment=existing,\n"
        "                            tariff=tariff,\n"
        "                        )\n"
        "                    except CheckoutQuoteConflictError:\n"
        "                        return None, \"unfinished_checkout_exists\"\n"
        "                    await _log_event_safe(\n"
        "                        session,\n"
        "                        existing.id,\n"
        "                        \"checkout_quote_reissued\",\n"
        "                        reason=f\"previous_quote:{old_quote_id}\",\n"
        "                        source=\"user_checkout_resume\",\n"
        "                    )\n"
        "                if existing.external_id and not existing.payment_url:\n"
        "                    await ensure_reconcile_payment_operation(\n"
        "                        session,\n"
        "                        existing,\n"
        "                        reason=\"resume_existing_checkout\",\n"
        "                    )\n"
        "                await session.flush()\n"
        "                return existing, existing.payment_url\n",
    )


def adjust_tests() -> None:
    path = "tests/test_payment_checkout_ux.py"
    replace_exact(path, "import unittest\n", "import unittest\nfrom pathlib import Path\n")
    replace_exact(
        path,
        "        user_cancel_requested_at=None,\n"
        "    ):\n",
        "        user_cancel_requested_at=None,\n"
        "        quote_age_minutes=0,\n"
        "    ):\n",
    )
    replace_exact(
        path,
        "        created = now_utc()\n",
        "        created = now_utc() - timedelta(minutes=quote_age_minutes)\n",
    )
    replace_exact(
        path,
        "                user_cancel_requested_at=now_utc(),\n"
        "            )\n"
        "            payment_id = payment.id\n"
        "            quote_id = quote.id\n",
        "                user_cancel_requested_at=now_utc(),\n"
        "                quote_age_minutes=20,\n"
        "            )\n"
        "            payment_id = payment.id\n"
        "            old_quote_id = quote.id\n",
    )
    replace_exact(
        path,
        "            quote = await session.get(TariffQuote, quote_id)\n"
        "            self.assertEqual(quote.status, \"active\")\n"
        "            self.assertIsNone(quote.diagnostic_reason)\n",
        "            old_quote = await session.get(TariffQuote, old_quote_id)\n"
        "            self.assertEqual(old_quote.status, \"cancelled\")\n"
        "            self.assertEqual(\n"
        "                old_quote.diagnostic_reason, \"checkout_abandoned_by_user\"\n"
        "            )\n"
        "            self.assertIsNone(old_quote.payment_id)\n"
        "            new_quote = await session.get(TariffQuote, existing.tariff_quote_id)\n"
        "            self.assertNotEqual(new_quote.id, old_quote_id)\n"
        "            self.assertEqual(new_quote.status, \"active\")\n"
        "            self.assertEqual(new_quote.payment_id, payment_id)\n"
        "            self.assertGreater(new_quote.expires_at, now_utc())\n"
        "            self.assertEqual(\n"
        "                await session.scalar(select(func.count(TariffQuote.id))), 2\n"
        "            )\n",
    )
    insertion_marker = "    async def test_reconcile_restores_redirect_url(self):\n"
    content = (ROOT / path).read_text(encoding="utf-8")
    if content.count(insertion_marker) != 1:
        raise RuntimeError("test insertion marker mismatch")
    new_test = '''    async def test_expired_checkout_is_not_reissued_after_tariff_change(self):\n        async with self.sessions.begin() as session:\n            payment, quote, _ = await self._provider_checkout(\n                session,\n                checkout_status=\"abandoned\",\n                quote_status=\"cancelled\",\n                diagnostic_reason=\"checkout_abandoned_by_user\",\n                user_cancel_requested_at=now_utc(),\n                quote_age_minutes=20,\n            )\n            payment_id = payment.id\n            quote_id = quote.id\n            tariff = await session.get(Tariff, self.tariff_id)\n            tariff.price_rub = 120\n\n        async with self.sessions.begin() as session:\n            existing, error = await PaymentService.create_yookassa_payment(\n                session=session,\n                user_id=self.user_id,\n                tariff_id=self.tariff_id,\n                amount=Decimal(\"120.00\"),\n                telegram_id=self.telegram_id,\n                bot_username=\"test_bot\",\n            )\n            self.assertIsNone(existing)\n            self.assertEqual(error, \"unfinished_checkout_exists\")\n            self.assertEqual(\n                await session.scalar(select(func.count(Payment.id))), 1\n            )\n            self.assertEqual(\n                await session.scalar(select(func.count(TariffQuote.id))), 1\n            )\n            payment = await session.get(Payment, payment_id)\n            quote = await session.get(TariffQuote, quote_id)\n            self.assertEqual(payment.tariff_quote_id, quote_id)\n            self.assertEqual(payment.checkout_status, \"abandoned\")\n            self.assertEqual(quote.status, \"cancelled\")\n            self.assertEqual(quote.payment_id, payment_id)\n\n'''
    (ROOT / path).write_text(
        content.replace(insertion_marker, new_test + insertion_marker),
        encoding="utf-8",
    )
    replace_exact(
        path,
        "        root = os.path.dirname(os.path.dirname(__file__))\n"
        "        keyboard = open(\n"
        "            os.path.join(root, \"bot/keyboards/payment.py\"),\n"
        "            encoding=\"utf-8\",\n"
        "        ).read()\n"
        "        routes = open(\n"
        "            os.path.join(root, \"bot/handlers/payment/yookassa_routes.py\"),\n"
        "            encoding=\"utf-8\",\n"
        "        ).read()\n",
        "        root = Path(__file__).resolve().parents[1]\n"
        "        keyboard = (root / \"bot/keyboards/payment.py\").read_text(\n"
        "            encoding=\"utf-8\"\n"
        "        )\n"
        "        routes = (root / \"bot/handlers/payment/yookassa_routes.py\").read_text(\n"
        "            encoding=\"utf-8\"\n"
        "        )\n",
    )


def main() -> None:
    add_quote_reissue_repository_boundary()
    adjust_reusable_checkout_validation()
    adjust_payment_service()
    adjust_tests()


if __name__ == "__main__":
    main()
