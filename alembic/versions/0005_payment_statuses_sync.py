"""Sync payments provider_status and fulfillment_status check constraints and backfill legacy rows.

Revision ID: 0005_payment_statuses_sync
Revises: 0004_referral_entitlements
Create Date: 2026-08-16 20:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_payment_statuses_sync"
down_revision: str = "0004_referral_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Backfill legacy / transitional fulfillment statuses in existing rows
    op.execute(
        "UPDATE public.payments "
        "SET fulfillment_status = 'processing' "
        "WHERE fulfillment_status = 'pending'"
    )
    # Safely backfill reversal_pending: only set to 'reversed' if total confirmed refunds >= payment amount,
    # otherwise set to 'processing' so it remains eligible for recovery and reconciliation.
    op.execute(
        "UPDATE public.payments "
        "SET fulfillment_status = CASE "
        "    WHEN ( "
        "        SELECT COALESCE(SUM(amount), 0) "
        "        FROM public.payment_refunds "
        "        WHERE payment_refunds.payment_id = payments.id "
        "          AND payment_refunds.provider_status = 'succeeded' "
        "    ) >= payments.amount THEN 'reversed' "
        "    ELSE 'processing' "
        "END "
        "WHERE fulfillment_status = 'reversal_pending'"
    )

    # 2. Drop existing check constraints if present
    op.execute(
        "ALTER TABLE public.payments "
        "DROP CONSTRAINT IF EXISTS ck_payments_provider_status"
    )
    op.execute(
        "ALTER TABLE public.payments "
        "DROP CONSTRAINT IF EXISTS ck_payments_fulfillment_status"
    )

    # 3. Add explicit synchronized check constraints
    op.execute(
        "ALTER TABLE public.payments "
        "ADD CONSTRAINT ck_payments_provider_status "
        "CHECK (provider_status IN ("
        "  'not_created', 'creating', 'pending', 'waiting_for_capture', "
        "  'succeeded', 'canceled', 'refunded', 'unknown', 'manual_review'"
        "))"
    )
    op.execute(
        "ALTER TABLE public.payments "
        "ADD CONSTRAINT ck_payments_fulfillment_status "
        "CHECK (fulfillment_status IN ("
        "  'not_ready', 'processing', 'succeeded', 'failed', 'reversed', 'manual_review'"
        "))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.payments "
        "DROP CONSTRAINT IF EXISTS ck_payments_provider_status"
    )
    op.execute(
        "ALTER TABLE public.payments "
        "DROP CONSTRAINT IF EXISTS ck_payments_fulfillment_status"
    )
    op.execute(
        "ALTER TABLE public.payments "
        "ADD CONSTRAINT ck_payments_provider_status "
        "CHECK (provider_status IN ("
        "  'not_created', 'creating', 'pending', 'succeeded', "
        "  'canceled', 'refunded', 'unknown', 'manual_review'"
        ")) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.payments "
        "ADD CONSTRAINT ck_payments_fulfillment_status "
        "CHECK (fulfillment_status IN ("
        "  'not_ready', 'pending', 'processing', 'succeeded', 'failed', 'reversed', 'reversal_pending', 'manual_review'"
        ")) NOT VALID"
    )
