"""Squashed v2 migration from 0044: unified ledger buckets, Standard SSOT, multi-hub WI, UserDevice.

Revision ID: 0045_v2_clean_unified
Revises: 0044_clean_auto_disabled_servers
Create Date: 2026-11-20 00:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_v2_clean_unified"
down_revision = "0044_clean_auto_disabled_servers"
branch_labels = None
depends_on = None


def _reconcile_bucket_deficits(bind, *, has_orders: bool) -> None:
    """General post-split bucket-deficit reconciliation pass.

    If any user still has (real_bal < 0 AND bonus_bal > 0) or (bonus_bal < 0 AND real_bal > 0),
    shift ONLY the exact deficit LEAST(ABS(neg_bal), pos_bal) across that user's purchase_debit
    rows from the negative bucket to the positive bucket (merging into an existing (order_id, to_bucket)
    row if present, or splitting the boundary row with ':deficit_split'). If bonus_bal < 0 AND real_bal > 0
    with no bonus purchase_debit rows left, insert an offsetting real/bonus rebalance pair so real >= 0,
    bonus >= 0, and real + bonus is 100% conserved.
    """
    imbalanced_users = bind.exec_driver_sql(
        """
        SELECT
            user_id,
            COALESCE(SUM(CASE WHEN bucket = 'real' THEN amount ELSE 0 END), 0) AS real_bal,
            COALESCE(SUM(CASE WHEN bucket = 'bonus' THEN amount ELSE 0 END), 0) AS bonus_bal
        FROM account_ledger_entries
        GROUP BY user_id
        HAVING (
            COALESCE(SUM(CASE WHEN bucket = 'real' THEN amount ELSE 0 END), 0) < 0
            AND COALESCE(SUM(CASE WHEN bucket = 'bonus' THEN amount ELSE 0 END), 0) > 0
        ) OR (
            COALESCE(SUM(CASE WHEN bucket = 'bonus' THEN amount ELSE 0 END), 0) < 0
            AND COALESCE(SUM(CASE WHEN bucket = 'real' THEN amount ELSE 0 END), 0) > 0
        )
        ORDER BY user_id
        """
    ).fetchall()

    for user_id, real_bal, bonus_bal in imbalanced_users:
        if real_bal < 0 and bonus_bal > 0:
            from_bucket = "real"
            to_bucket = "bonus"
            rem_deficit = min(abs(real_bal), bonus_bal)
        elif bonus_bal < 0 and real_bal > 0:
            from_bucket = "bonus"
            to_bucket = "real"
            rem_deficit = min(abs(bonus_bal), real_bal)
        else:
            continue

        debits = bind.execute(
            sa.text(
                """
                SELECT id, amount, currency, order_id, idempotency_key
                FROM account_ledger_entries
                WHERE user_id = :user_id
                  AND entry_type = 'purchase_debit'
                  AND bucket = :from_bucket
                  AND amount < 0
                ORDER BY created_at DESC, id DESC
                """
            ),
            {"user_id": user_id, "from_bucket": from_bucket},
        ).fetchall()

        for entry_id, amount, _currency, order_id, _idem_key in debits:
            if rem_deficit <= 0:
                break
            debit_abs = abs(amount)
            shift_amt = min(debit_abs, rem_deficit)
            if shift_amt <= 0:
                continue

            target_row = None
            if order_id is not None:
                target_row = bind.execute(
                    sa.text(
                        """
                        SELECT id
                        FROM account_ledger_entries
                        WHERE user_id = :user_id
                          AND order_id = :order_id
                          AND entry_type = 'purchase_debit'
                          AND bucket = :to_bucket
                        LIMIT 1
                        """
                    ),
                    {"user_id": user_id, "order_id": order_id, "to_bucket": to_bucket},
                ).fetchone()

            if target_row is not None:
                target_id = target_row[0]
                bind.execute(
                    sa.text(
                        "UPDATE account_ledger_entries SET amount = amount - :shift_amt WHERE id = :target_id"
                    ),
                    {"shift_amt": shift_amt, "target_id": target_id},
                )
                if shift_amt == debit_abs:
                    bind.execute(
                        sa.text("DELETE FROM account_ledger_entries WHERE id = :entry_id"),
                        {"entry_id": entry_id},
                    )
                else:
                    bind.execute(
                        sa.text(
                            "UPDATE account_ledger_entries SET amount = :new_amount WHERE id = :entry_id"
                        ),
                        {"new_amount": -(debit_abs - shift_amt), "entry_id": entry_id},
                    )
            else:
                if shift_amt == debit_abs:
                    bind.execute(
                        sa.text(
                            "UPDATE account_ledger_entries SET bucket = :to_bucket WHERE id = :entry_id"
                        ),
                        {"to_bucket": to_bucket, "entry_id": entry_id},
                    )
                else:
                    split_key = f"deficit_split:{entry_id}:{to_bucket}"
                    inserted_split = bind.execute(
                        sa.text(
                            """
                            INSERT INTO account_ledger_entries (
                                user_id,
                                entry_type,
                                bucket,
                                amount,
                                currency,
                                order_id,
                                idempotency_key,
                                metadata,
                                created_at
                            )
                            SELECT
                                e.user_id,
                                'purchase_debit',
                                CAST(:to_bucket AS varchar),
                                CAST(:neg_shift_amt AS numeric),
                                e.currency,
                                e.order_id,
                                CAST(:split_key AS varchar),
                                e.metadata,
                                e.created_at
                            FROM account_ledger_entries e
                            WHERE e.id = :entry_id
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM account_ledger_entries existing
                                  WHERE existing.idempotency_key = CAST(:split_key AS varchar)
                              )
                            RETURNING id
                            """
                        ),
                        {
                            "to_bucket": to_bucket,
                            "neg_shift_amt": -shift_amt,
                            "split_key": split_key,
                            "entry_id": entry_id,
                        },
                    ).scalar()
                    if inserted_split is not None:
                        bind.execute(
                            sa.text(
                                "UPDATE account_ledger_entries SET amount = :new_amount WHERE id = :entry_id"
                            ),
                            {"new_amount": -(debit_abs - shift_amt), "entry_id": entry_id},
                        )
            rem_deficit -= shift_amt

        if rem_deficit > 0 and from_bucket == "bonus" and to_bucket == "real" and has_orders:
            # Fallback when bonus_bal < 0 AND real_bal > 0 with no bonus purchase_debit rows left
            # (e.g., a negative admin_adjustment on a user whose funds came from payment_credit).
            order_row = bind.execute(
                sa.text(
                    """
                    SELECT order_id
                    FROM account_ledger_entries
                    WHERE user_id = :user_id
                      AND entry_type = 'payment_credit'
                      AND order_id IS NOT NULL
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user_id},
            ).fetchone()
            if order_row is not None and order_row[0] is not None:
                rebal_order_id = order_row[0]
            else:
                rebal_order_id = bind.execute(
                    sa.text(
                        """
                        INSERT INTO orders (
                            id,
                            user_id,
                            service_type,
                            payment_method,
                            status,
                            amount_rub,
                            external_id,
                            metadata,
                            created_at,
                            paid_at,
                            refunded_at
                        )
                        VALUES (
                            gen_random_uuid(),
                            CAST(:user_id AS integer),
                            'topup',
                            'yookassa',
                            'refunded',
                            CAST(:rem_deficit AS numeric),
                            CAST(:ext_id AS varchar),
                            jsonb_build_object('legacy_deficit_rebalance_user_id', CAST(:user_id AS integer)),
                            now(),
                            now(),
                            now()
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "user_id": user_id,
                        "rem_deficit": rem_deficit,
                        "ext_id": f"legacy_deficit_rebalance:{user_id}",
                    },
                ).scalar()

            real_key = f"legacy_deficit_rebalance:{user_id}:real"
            bonus_key = f"legacy_deficit_rebalance:{user_id}:bonus"
            bind.execute(
                sa.text(
                    """
                    INSERT INTO account_ledger_entries (
                        user_id,
                        entry_type,
                        bucket,
                        amount,
                        currency,
                        order_id,
                        idempotency_key,
                        metadata,
                        created_at
                    )
                    SELECT CAST(:user_id AS integer), 'refund_debit', 'real',
                           CAST(:neg_rem_deficit AS numeric), 'RUB',
                           CAST(:rebal_order_id AS uuid), CAST(:real_key AS varchar),
                           jsonb_build_object('source', 'legacy_deficit_rebalance'), now()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM account_ledger_entries
                        WHERE idempotency_key = CAST(:real_key AS varchar)
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "neg_rem_deficit": -rem_deficit,
                    "rebal_order_id": rebal_order_id,
                    "real_key": real_key,
                },
            )
            bind.execute(
                sa.text(
                    """
                    INSERT INTO account_ledger_entries (
                        user_id,
                        entry_type,
                        bucket,
                        amount,
                        currency,
                        order_id,
                        idempotency_key,
                        metadata,
                        created_at
                    )
                    SELECT CAST(:user_id AS integer), 'admin_adjustment', 'bonus',
                           CAST(:rem_deficit AS numeric), 'RUB',
                           NULL, CAST(:bonus_key AS varchar),
                           jsonb_build_object('source', 'legacy_deficit_rebalance'), now()
                    WHERE NOT EXISTS (
                        SELECT 1 FROM account_ledger_entries
                        WHERE idempotency_key = CAST(:bonus_key AS varchar)
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "rem_deficit": rem_deficit,
                    "bonus_key": bonus_key,
                },
            )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # 1. Drop legacy tables superseded by v2 architecture (account_ledger_allocations preserved until step 3)
    for legacy_tbl in (
        "white_internet_orphan_cleanups",
        "api_operations",
        "hub_messages",
        "admin_operation_idempotency",
        "maintenance_mode",
    ):
        if legacy_tbl in tables:
            bind.exec_driver_sql(f"DROP TABLE IF EXISTS {legacy_tbl} CASCADE")

    # 2. Orders: awg -> standard, drop tariff_id, add idempotency_key & partially_refunded status
    if "orders" in tables:
        order_cols = {c["name"] for c in insp.get_columns("orders")}
        order_fks = {fk["name"] for fk in insp.get_foreign_keys("orders") if fk.get("name")}
        order_checks = {c["name"] for c in insp.get_check_constraints("orders")}
        order_indexes = {i["name"] for i in insp.get_indexes("orders")}

        bind.exec_driver_sql("UPDATE orders SET service_type = 'standard' WHERE service_type = 'awg'")

        for fk_name in list(order_fks):
            if "tariff" in fk_name:
                op.drop_constraint(fk_name, "orders", type_="foreignkey")
        if "tariff_id" in order_cols:
            op.drop_column("orders", "tariff_id")

        if "idempotency_key" not in order_cols:
            op.add_column("orders", sa.Column("idempotency_key", sa.String(128), nullable=True))
        if "uq_orders_user_idempotency_key" not in order_indexes:
            op.create_index(
                "uq_orders_user_idempotency_key",
                "orders",
                ["user_id", "idempotency_key"],
                unique=True,
                postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            )

        if "ck_orders_service_type" in order_checks:
            op.drop_constraint("ck_orders_service_type", "orders", type_="check")
        op.create_check_constraint(
            "ck_orders_service_type",
            "orders",
            "service_type IN ('standard', 'white_internet', 'topup')",
        )

        if "ck_orders_status" in order_checks:
            op.drop_constraint("ck_orders_status", "orders", type_="check")
        bind.exec_driver_sql("UPDATE orders SET status = 'canceled' WHERE status = 'expired'")
        op.create_check_constraint(
            "ck_orders_status",
            "orders",
            "status IN ('pending', 'paid', 'canceled', 'refunded', 'partially_refunded')",
        )

    # 3. Account ledger entries: real/bonus buckets without payments/allocations tables
    if "account_ledger_entries" in tables:
        ledger_cols = {c["name"] for c in insp.get_columns("account_ledger_entries")}
        ledger_fks = {fk["name"] for fk in insp.get_foreign_keys("account_ledger_entries") if fk.get("name")}
        ledger_indexes = {i["name"] for i in insp.get_indexes("account_ledger_entries")}

        for fk_name in list(ledger_fks):
            if any(k in fk_name for k in ("payment", "quote", "reversal")):
                op.drop_constraint(fk_name, "account_ledger_entries", type_="foreignkey")
        for idx_name in (
            "uq_account_ledger_order_debit",
            "ix_account_ledger_entries_payment_id",
            "uq_account_ledger_payment_credit",
            "uq_account_ledger_purchase_debit",
            "uq_account_ledger_purchase_reversal",
            "ix_account_ledger_payment_debits",
            "ix_account_ledger_entries_quote_id",
        ):
            if idx_name in ledger_indexes:
                op.drop_index(idx_name, table_name="account_ledger_entries")

        # Drop legacy shape/type/bucket constraints BEFORE running any UPDATE/INSERT on account_ledger_entries
        # so rows still carrying payment_id or reversal_of_id never trigger CheckViolation (P0-1).
        for ck_name in (
            "ck_account_ledger_bucket",
            "ck_account_ledger_entry_shape",
            "ck_account_ledger_entry_type",
        ):
            bind.exec_driver_sql(f"ALTER TABLE account_ledger_entries DROP CONSTRAINT IF EXISTS {ck_name}")

        if "bucket" not in ledger_cols:
            op.add_column(
                "account_ledger_entries",
                sa.Column("bucket", sa.String(10), nullable=True),
            )

        # Temporarily disable append-only trigger for one-time normalization and backfill
        bind.exec_driver_sql("ALTER TABLE account_ledger_entries DISABLE TRIGGER account_ledger_append_only")

        # P1-7: Preserve succeeded legacy payments in orders and link account_ledger_entries.order_id
        # before dropping payment_id and the payments table.
        if "payments" in tables and "orders" in tables and "payment_id" in ledger_cols:
            payment_cols = {c["name"] for c in insp.get_columns("payments")}
            order_cols_now = {c["name"] for c in insp.get_columns("orders")}
            provider_expr = (
                "p.provider"
                if "provider" in payment_cols
                else (
                    "COALESCE(p.payment_method, 'yookassa')"
                    if "payment_method" in payment_cols
                    else "'yookassa'"
                )
            )
            paid_at_expr = (
                "COALESCE(p.updated_at, p.created_at)"
                if "updated_at" in payment_cols
                else "p.created_at"
            )
            topup_ctx_expr = (
                "COALESCE(p.topup_context, '{}'::jsonb)"
                if "topup_context" in payment_cols
                else "'{}'::jsonb"
            )
            updated_at_col_sql = ", updated_at" if "updated_at" in order_cols_now else ""
            updated_at_val_sql = f", {paid_at_expr}" if "updated_at" in order_cols_now else ""

            bind.exec_driver_sql(
                f"""
                WITH inserted_legacy_orders AS (
                    INSERT INTO orders (
                        id,
                        user_id,
                        service_type,
                        payment_method,
                        status,
                        amount_rub,
                        external_id,
                        metadata,
                        created_at,
                        paid_at{updated_at_col_sql}
                    )
                    SELECT
                        gen_random_uuid(),
                        p.user_id,
                        'topup',
                        'yookassa',
                        'paid',
                        p.amount,
                        'legacy_payment:' || p.id::text,
                        {topup_ctx_expr} || jsonb_build_object(
                            'legacy_payment_id', p.id,
                            'legacy_provider', {provider_expr}
                        ),
                        p.created_at,
                        {paid_at_expr}{updated_at_val_sql}
                    FROM payments p
                    WHERE p.provider_status = 'succeeded'
                      AND p.amount > 0
                      AND EXISTS (
                          SELECT 1
                          FROM account_ledger_entries ale
                          WHERE ale.payment_id = p.id
                            AND ale.order_id IS NULL
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM orders o
                          WHERE o.external_id = 'legacy_payment:' || p.id::text
                      )
                    RETURNING id, (metadata->>'legacy_payment_id')::bigint AS legacy_payment_id
                )
                UPDATE account_ledger_entries ale
                SET order_id = ilo.id
                FROM inserted_legacy_orders ilo
                WHERE ale.payment_id = ilo.legacy_payment_id
                  AND ale.order_id IS NULL
                """
            )
            bind.exec_driver_sql(
                """
                UPDATE account_ledger_entries ale
                SET order_id = o.id
                FROM orders o
                WHERE ale.order_id IS NULL
                  AND ale.payment_id IS NOT NULL
                  AND o.external_id = 'legacy_payment:' || ale.payment_id::text
                """
            )

        # Normalize referral_bonus / purchase_reversal to admin_adjustment first
        bind.exec_driver_sql(
            """
            UPDATE account_ledger_entries
            SET entry_type = 'admin_adjustment'
            WHERE entry_type IN ('referral_bonus', 'purchase_reversal')
            """
        )

        # Normalize chargeback_debit with order_id IS NOT NULL to refund_debit
        bind.exec_driver_sql(
            """
            UPDATE account_ledger_entries
            SET entry_type = 'refund_debit'
            WHERE entry_type = 'chargeback_debit' AND order_id IS NOT NULL
            """
        )

        # For any legacy refund_debit / chargeback_debit with order_id IS NULL:
        # If the user has real payment_credit entries (or linked via payment_id), link to an order and keep
        # as refund_debit in the 'real' bucket; only convert to bonus admin_adjustment if user has no real credits.
        if "orders" in tables:
            bind.exec_driver_sql(
                """
                WITH orphan_real_refunds AS (
                    SELECT e.id AS entry_id, e.user_id, ABS(e.amount) AS amt, e.created_at
                    FROM account_ledger_entries e
                    WHERE e.entry_type IN ('refund_debit', 'chargeback_debit')
                      AND e.order_id IS NULL
                      AND EXISTS (
                          SELECT 1
                          FROM account_ledger_entries c
                          WHERE c.user_id = e.user_id
                            AND c.entry_type = 'payment_credit'
                      )
                ),
                inserted_refund_orders AS (
                    INSERT INTO orders (
                        id,
                        user_id,
                        service_type,
                        payment_method,
                        status,
                        amount_rub,
                        external_id,
                        metadata,
                        created_at,
                        paid_at,
                        refunded_at
                    )
                    SELECT
                        gen_random_uuid(),
                        orr.user_id,
                        'topup',
                        'yookassa',
                        'refunded',
                        orr.amt,
                        'legacy_refund_entry:' || orr.entry_id::text,
                        jsonb_build_object('legacy_refund_entry_id', orr.entry_id),
                        orr.created_at,
                        orr.created_at,
                        orr.created_at
                    FROM orphan_real_refunds orr
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM orders o
                        WHERE o.external_id = 'legacy_refund_entry:' || orr.entry_id::text
                    )
                    RETURNING id, (metadata->>'legacy_refund_entry_id')::bigint AS entry_id
                )
                UPDATE account_ledger_entries ale
                SET order_id = iro.id,
                    entry_type = 'refund_debit',
                    bucket = 'real'
                FROM inserted_refund_orders iro
                WHERE ale.id = iro.entry_id
                """
            )
        bind.exec_driver_sql(
            """
            UPDATE account_ledger_entries e
            SET entry_type = 'admin_adjustment',
                bucket = 'bonus'
            WHERE e.entry_type IN ('refund_debit', 'chargeback_debit')
              AND e.order_id IS NULL
            """
        )

        # Assign base buckets cleanly
        bind.exec_driver_sql(
            """
            UPDATE account_ledger_entries
            SET bucket = CASE
                WHEN entry_type IN ('payment_credit', 'refund_debit') THEN 'real'
                WHEN entry_type = 'admin_adjustment' THEN 'bonus'
                ELSE 'real'
            END
            WHERE bucket IS NULL
            """
        )

        # P0-2: Exact real/bonus split on purchase_debit using account_ledger_allocations
        if "account_ledger_allocations" in tables:
            # 1. Split allocated purchase_debit rows where real_part > 0 AND bonus_part > 0
            #    (computing real_part = LEAST(ABS(e.amount), at.real_alloc) and
            #    bonus_part = ABS(e.amount) - LEAST(ABS(e.amount), at.real_alloc) so
            #    (-real_part) + (-bonus_part) == e.amount is 100% conserved even on partial allocations).
            bind.exec_driver_sql(
                """
                WITH alloc_totals AS (
                    SELECT
                        a.debit_entry_id,
                        COALESCE(SUM(CASE WHEN c.entry_type = 'payment_credit' THEN a.amount ELSE 0 END), 0) AS real_alloc,
                        COALESCE(SUM(CASE WHEN c.entry_type = 'admin_adjustment' THEN a.amount ELSE 0 END), 0) AS bonus_alloc
                    FROM account_ledger_allocations a
                    JOIN account_ledger_entries c ON a.credit_entry_id = c.id
                    GROUP BY a.debit_entry_id
                ),
                alloc_plan AS (
                    SELECT
                        e.id AS debit_entry_id,
                        LEAST(ABS(e.amount), at.real_alloc) AS real_part,
                        ABS(e.amount) - LEAST(ABS(e.amount), at.real_alloc) AS bonus_part
                    FROM account_ledger_entries e
                    JOIN alloc_totals at ON at.debit_entry_id = e.id
                    WHERE e.entry_type = 'purchase_debit'
                      AND (at.real_alloc > 0 OR at.bonus_alloc > 0)
                ),
                split_updated AS (
                    UPDATE account_ledger_entries e
                    SET bucket = 'real',
                        amount = -ap.real_part
                    FROM alloc_plan ap
                    WHERE e.id = ap.debit_entry_id
                      AND ap.real_part > 0
                      AND ap.bonus_part > 0
                    RETURNING
                        e.user_id,
                        e.currency,
                        e.order_id,
                        e.idempotency_key,
                        e.metadata,
                        e.created_at,
                        ap.bonus_part
                )
                INSERT INTO account_ledger_entries (
                    user_id,
                    entry_type,
                    bucket,
                    amount,
                    currency,
                    order_id,
                    idempotency_key,
                    metadata,
                    created_at
                )
                SELECT
                    su.user_id,
                    'purchase_debit',
                    'bonus',
                    -su.bonus_part,
                    su.currency,
                    su.order_id,
                    CASE
                        WHEN su.idempotency_key IS NOT NULL THEN su.idempotency_key || ':bonus_split'
                        ELSE NULL
                    END,
                    su.metadata,
                    su.created_at
                FROM split_updated su
                WHERE su.idempotency_key IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                       FROM account_ledger_entries existing
                       WHERE existing.idempotency_key = su.idempotency_key || ':bonus_split'
                   )
                """
            )

            # 2. Pure-bonus allocated purchase_debit rows (real_alloc = 0 AND bonus_alloc > 0)
            bind.exec_driver_sql(
                """
                WITH alloc_totals AS (
                    SELECT
                        a.debit_entry_id,
                        COALESCE(SUM(CASE WHEN c.entry_type = 'payment_credit' THEN a.amount ELSE 0 END), 0) AS real_alloc,
                        COALESCE(SUM(CASE WHEN c.entry_type = 'admin_adjustment' THEN a.amount ELSE 0 END), 0) AS bonus_alloc
                    FROM account_ledger_allocations a
                    JOIN account_ledger_entries c ON a.credit_entry_id = c.id
                    GROUP BY a.debit_entry_id
                )
                UPDATE account_ledger_entries e
                SET bucket = 'bonus'
                FROM alloc_totals at
                WHERE e.id = at.debit_entry_id
                  AND e.entry_type = 'purchase_debit'
                  AND at.real_alloc = 0
                  AND at.bonus_alloc > 0
                """
            )

            # 3. Unallocated purchase_debit rows (real_alloc = 0 AND bonus_alloc = 0):
            #    Use only remaining real pool (after allocated real debits) so allocated rows are never flipped.
            bind.exec_driver_sql(
                """
                WITH unalloc_debits AS (
                    SELECT
                        e.id,
                        e.user_id,
                        ABS(e.amount) AS debit_abs,
                        e.currency,
                        e.order_id,
                        e.idempotency_key,
                        e.metadata,
                        e.created_at,
                        SUM(ABS(e.amount)) OVER (
                            PARTITION BY e.user_id
                            ORDER BY e.created_at ASC, e.id ASC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        ) - ABS(e.amount) AS prior_unalloc_abs
                    FROM account_ledger_entries e
                    WHERE e.entry_type = 'purchase_debit'
                      AND e.bucket = 'real'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM account_ledger_allocations a
                          WHERE a.debit_entry_id = e.id
                      )
                ),
                user_real_pool AS (
                    SELECT
                        u_ids.user_id,
                        GREATEST(
                            0,
                            COALESCE(
                                SUM(
                                    CASE
                                        WHEN e.bucket = 'real'
                                         AND NOT EXISTS (
                                             SELECT 1 FROM unalloc_debits ud WHERE ud.id = e.id
                                         )
                                        THEN e.amount
                                        ELSE 0
                                    END
                                ),
                                0
                            )
                        ) AS avail_real
                    FROM (SELECT DISTINCT user_id FROM unalloc_debits) u_ids
                    LEFT JOIN account_ledger_entries e ON e.user_id = u_ids.user_id
                    GROUP BY u_ids.user_id
                ),
                unalloc_plan AS (
                    SELECT
                        ud.*,
                        LEAST(
                            ud.debit_abs,
                            GREATEST(0, urp.avail_real - ud.prior_unalloc_abs)
                        ) AS real_part,
                        ud.debit_abs - LEAST(
                            ud.debit_abs,
                            GREATEST(0, urp.avail_real - ud.prior_unalloc_abs)
                        ) AS bonus_part
                    FROM unalloc_debits ud
                    JOIN user_real_pool urp ON urp.user_id = ud.user_id
                ),
                pure_bonus_unalloc AS (
                    UPDATE account_ledger_entries e
                    SET bucket = 'bonus'
                    FROM unalloc_plan up
                    WHERE e.id = up.id
                      AND up.real_part = 0
                      AND up.bonus_part > 0
                    RETURNING e.id
                ),
                split_unalloc AS (
                    UPDATE account_ledger_entries e
                    SET bucket = 'real',
                        amount = -up.real_part
                    FROM unalloc_plan up
                    WHERE e.id = up.id
                      AND up.real_part > 0
                      AND up.bonus_part > 0
                    RETURNING
                        e.user_id,
                        e.currency,
                        e.order_id,
                        e.idempotency_key,
                        e.metadata,
                        e.created_at,
                        up.bonus_part
                )
                INSERT INTO account_ledger_entries (
                    user_id,
                    entry_type,
                    bucket,
                    amount,
                    currency,
                    order_id,
                    idempotency_key,
                    metadata,
                    created_at
                )
                SELECT
                    su.user_id,
                    'purchase_debit',
                    'bonus',
                    -su.bonus_part,
                    su.currency,
                    su.order_id,
                    CASE
                        WHEN su.idempotency_key IS NOT NULL THEN su.idempotency_key || ':bonus_split'
                        ELSE NULL
                    END,
                    su.metadata,
                    su.created_at
                FROM split_unalloc su
                WHERE su.idempotency_key IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                       FROM account_ledger_entries existing
                       WHERE existing.idempotency_key = su.idempotency_key || ':bonus_split'
                   )
                """
            )
        else:
            bind.exec_driver_sql(
                """
                UPDATE account_ledger_entries e
                SET bucket = 'bonus'
                WHERE e.entry_type = 'purchase_debit'
                  AND e.bucket = 'real'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM account_ledger_entries c
                      WHERE c.user_id = e.user_id
                        AND c.entry_type = 'payment_credit'
                  )
                """
            )

        # 4. General post-split bucket-deficit reconciliation pass
        _reconcile_bucket_deficits(bind, has_orders="orders" in tables)

        op.alter_column("account_ledger_entries", "bucket", nullable=False, server_default=sa.text("'real'"))
        bind.exec_driver_sql("ALTER TABLE account_ledger_entries ENABLE TRIGGER account_ledger_append_only")

        # Now drop legacy account_ledger_allocations after clean data migration
        if "account_ledger_allocations" in tables:
            bind.exec_driver_sql("DROP TRIGGER IF EXISTS account_allocations_append_only ON account_ledger_allocations")
            bind.exec_driver_sql("DROP TRIGGER IF EXISTS account_allocation_validate ON account_ledger_allocations")
            bind.exec_driver_sql("DROP FUNCTION IF EXISTS validate_account_allocation() CASCADE")
            bind.exec_driver_sql("DROP FUNCTION IF EXISTS reject_account_allocation_change() CASCADE")
            bind.exec_driver_sql("DROP TABLE IF EXISTS account_ledger_allocations CASCADE")

        for col in ("payment_id", "quote_id", "reversal_of_id"):
            if col in ledger_cols:
                op.drop_column("account_ledger_entries", col)

        op.create_check_constraint(
            "ck_account_ledger_entry_type",
            "account_ledger_entries",
            "entry_type IN ('payment_credit', 'purchase_debit', 'refund_debit', 'admin_adjustment')",
        )
        op.create_check_constraint(
            "ck_account_ledger_bucket",
            "account_ledger_entries",
            "bucket IN ('real', 'bonus')",
        )
        op.create_check_constraint(
            "ck_account_ledger_entry_shape",
            "account_ledger_entries",
            "(entry_type = 'payment_credit' AND bucket = 'real' AND amount > 0) OR "
            "(entry_type = 'purchase_debit' AND amount < 0 AND order_id IS NOT NULL) OR "
            "(entry_type = 'refund_debit' AND amount < 0 AND order_id IS NOT NULL AND bucket = 'real') OR "
            "(entry_type = 'admin_adjustment' AND bucket = 'bonus')",
        )
        if "ix_account_ledger_user_bucket" not in ledger_indexes:
            op.create_index(
                "ix_account_ledger_user_bucket",
                "account_ledger_entries",
                ["user_id", "bucket"],
            )
        # P1-6: Recreate partial unique index uq_account_ledger_order_debit on (order_id, bucket)
        op.create_index(
            "uq_account_ledger_order_debit",
            "account_ledger_entries",
            ["order_id", "bucket"],
            unique=True,
            postgresql_where=sa.text("entry_type = 'purchase_debit' AND order_id IS NOT NULL"),
        )

    for legacy_tbl in ("payments", "tariffs"):
        if legacy_tbl in tables:
            bind.exec_driver_sql(f"DROP TABLE IF EXISTS {legacy_tbl} CASCADE")

    # 4. Servers: purge incompatible v1 servers on 0044->0045 cutover, drop max_clients, add role
    v1_servers_purged = False
    if "servers" in tables:
        srv_cols = {c["name"] for c in insp.get_columns("servers")}
        if "role" not in srv_cols:
            bind.exec_driver_sql("DELETE FROM servers")
            v1_servers_purged = True
        else:
            bind.exec_driver_sql("DELETE FROM servers WHERE protocol NOT IN ('vless', 'xray')")
        srv_indexes = {i["name"] for i in insp.get_indexes("servers")}
        if "max_clients" in srv_cols:
            op.drop_column("servers", "max_clients")
        if "role" not in srv_cols:
            op.add_column(
                "servers",
                sa.Column("role", sa.String(30), nullable=False, server_default=sa.text("'ru_hub'")),
            )
        else:
            op.alter_column(
                "servers",
                "role",
                type_=sa.String(30),
                existing_nullable=False,
                nullable=False,
            )
        if "ix_servers_role" not in srv_indexes:
            op.create_index("ix_servers_role", "servers", ["role"])
        srv_checks = {c["name"] for c in insp.get_check_constraints("servers")}
        if "ck_servers_role" not in srv_checks:
            op.create_check_constraint(
                "ck_servers_role",
                "servers",
                "role IN ('ru_hub', 'eu_hub', 'exit_node')",
            )

    # 5. VlessSubscriptions SSOT + backfill from users (preserving 4h grace)
    if "vless_subscriptions" in tables:
        vs_cols = {c["name"] for c in insp.get_columns("vless_subscriptions")}
        vs_indexes = {i["name"] for i in insp.get_indexes("vless_subscriptions")}

        if "active_hwids" in vs_cols:
            op.drop_column("vless_subscriptions", "active_hwids")
        if v1_servers_purged and "node_traffic_snapshots" in vs_cols:
            bind.exec_driver_sql("UPDATE vless_subscriptions SET node_traffic_snapshots = '{}'::jsonb")

        add_vs_cols = [
            ("expires_at", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)),
            ("device_limit", sa.Column("device_limit", sa.Integer(), nullable=False, server_default=sa.text("0"))),
            ("downgrade_cooldown_until", sa.Column("downgrade_cooldown_until", sa.DateTime(timezone=True), nullable=True)),
            ("auto_renew_enabled", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_3d", sa.Column("notified_3d", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_1d", sa.Column("notified_1d", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_2h", sa.Column("notified_2h", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("notified_expired", sa.Column("notified_expired", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
        ]
        for col_name, col_def in add_vs_cols:
            if col_name not in vs_cols:
                op.add_column("vless_subscriptions", col_def)

        if "ix_vless_subscriptions_pending_revoked" not in vs_indexes:
            op.create_index(
                "ix_vless_subscriptions_pending_revoked",
                "vless_subscriptions",
                ["id"],
                postgresql_where=sa.text("pending_revoked_uuids != '[]'::jsonb"),
            )
        if "ix_vless_subscriptions_expires_at" not in vs_indexes:
            op.create_index(
                "ix_vless_subscriptions_expires_at",
                "vless_subscriptions",
                ["expires_at"],
                postgresql_where=sa.text("expires_at IS NOT NULL"),
            )

        op.alter_column("vless_subscriptions", "is_active", server_default=sa.text("false"))

        if "users" in tables:
            user_cols = {c["name"] for c in insp.get_columns("users")}
            if "subscription_end" in user_cols:
                bind.exec_driver_sql(
                    """
                    INSERT INTO vless_subscriptions (
                        user_id, token, uuid, is_active,
                        expires_at, device_limit, auto_renew_enabled,
                        notified_3d, notified_1d, notified_2h, notified_expired
                    )
                    SELECT
                        u.id,
                        substr(md5(u.id::text || clock_timestamp()::text || random()::text), 1, 32),
                        (
                            substr(h, 1, 8) || '-' || substr(h, 9, 4) || '-4' || substr(h, 14, 3) ||
                            '-a' || substr(h, 18, 3) || '-' || substr(h, 21, 12)
                        ),
                        (COALESCE(u.is_deleted, false) = false AND u.subscription_end IS NOT NULL AND u.subscription_end > now() - interval '4 hours'),
                        u.subscription_end,
                        COALESCE(u.device_limit, 0),
                        false,
                        COALESCE(u.notified_3d, false),
                        COALESCE(u.notified_1d, false),
                        COALESCE(u.notified_2h, false),
                        COALESCE(u.notified_expired, false)
                    FROM (
                        SELECT *, md5(id::text || random()::text || clock_timestamp()::text) AS h
                        FROM users
                        WHERE subscription_end IS NOT NULL
                           OR COALESCE(device_limit, 0) > 0
                    ) u
                    WHERE NOT EXISTS (
                        SELECT 1 FROM vless_subscriptions vs WHERE vs.user_id = u.id
                    )
                    """
                )
                bind.exec_driver_sql(
                    """
                    UPDATE vless_subscriptions vs
                    SET
                        expires_at = u.subscription_end,
                        device_limit = COALESCE(u.device_limit, 0),
                        is_active = (COALESCE(u.is_deleted, false) = false AND u.subscription_end IS NOT NULL AND u.subscription_end > now() - interval '4 hours'),
                        notified_3d = COALESCE(u.notified_3d, false),
                        notified_1d = COALESCE(u.notified_1d, false),
                        notified_2h = COALESCE(u.notified_2h, false),
                        notified_expired = COALESCE(u.notified_expired, false)
                    FROM users u
                    WHERE vs.user_id = u.id
                    """
                )

    # 6. Drop legacy & Standard columns from users
    if "users" in tables:
        user_cols = {c["name"] for c in insp.get_columns("users")}
        for idx_name in (
            "ix_users_active_subscription",
            "ix_users_expiring_subscription",
            "ix_users_expired_grace_notify",
            "ix_users_current_tariff_id",
        ):
            bind.exec_driver_sql(f"DROP INDEX IF EXISTS {idx_name}")
        for col in (
            "subscription_end",
            "device_limit",
            "current_tariff_id",
            "last_trial_reset_at",
            "show_in_leaderboard",
            "notified_3d",
            "notified_1d",
            "notified_2h",
            "notified_expired",
            "notified_grace_12h",
            "device_creations_today",
            "last_creation_date",
            "archived_device_traffic",
            "is_banned",
        ):
            if col in user_cols:
                op.drop_column("users", col)

    # 7. White Internet subscriptions: remove server pinning, add multi-hub snapshots & device revocation
    if "white_internet_subscriptions" in tables:
        wi_cols = {c["name"] for c in insp.get_columns("white_internet_subscriptions")}
        wi_fks = {fk["name"] for fk in insp.get_foreign_keys("white_internet_subscriptions") if fk.get("name")}
        for fk_name in list(wi_fks):
            if "origin_node" in fk_name or "hub_server" in fk_name:
                op.drop_constraint(fk_name, "white_internet_subscriptions", type_="foreignkey")
        for idx_name in (
            "ix_white_internet_subscriptions_origin_node_id",
            "ix_white_internet_subscriptions_hub_server_id",
            "ix_white_internet_subscriptions_active_hwids",
            "ix_wi_subscriptions_pending_hard_delete",
        ):
            bind.exec_driver_sql(f"DROP INDEX IF EXISTS {idx_name}")
        for col in (
            "origin_node_id",
            "hub_server_id",
            "active_hwids",
            "last_device_reset_at",
            "is_trial",
            "pending_hard_delete",
            "last_reconciled_node_epoch",
            "last_uplink_snapshot",
            "last_downlink_snapshot",
            "traffic_stats_epoch",
        ):
            if col in wi_cols:
                op.drop_column("white_internet_subscriptions", col)

        add_wi_cols = [
            ("node_traffic_snapshots", sa.Column("node_traffic_snapshots", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb"))),
            ("pending_device_limit", sa.Column("pending_device_limit", sa.Integer(), nullable=True)),
            ("pending_from", sa.Column("pending_from", sa.DateTime(timezone=True), nullable=True)),
            ("auto_renew_enabled", sa.Column("auto_renew_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("node_sync_state", sa.Column("node_sync_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb"))),
            ("pending_revoked_uuids", sa.Column("pending_revoked_uuids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb"))),
        ]
        for col_name, col_def in add_wi_cols:
            if col_name not in wi_cols:
                op.add_column("white_internet_subscriptions", col_def)

        wi_checks = {c["name"] for c in insp.get_check_constraints("white_internet_subscriptions")}
        if "ck_wi_subs_pending_device_limit" not in wi_checks:
            op.create_check_constraint(
                "ck_wi_subs_pending_device_limit",
                "white_internet_subscriptions",
                "pending_device_limit IS NULL OR (pending_device_limit >= 1 AND pending_device_limit <= 5)",
            )

    # 8. Create user_devices
    if "user_devices" not in tables:
        op.create_table(
            "user_devices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("hwid", sa.String(128), nullable=False),
            sa.Column("device_uuid", sa.String(36), nullable=False),
            sa.Column("device_name", sa.String(128), nullable=True),
            sa.Column("platform", sa.String(64), nullable=True),
            sa.Column("os_version", sa.String(64), nullable=True),
            sa.Column("app_version", sa.String(64), nullable=True),
            sa.Column("bi_priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "hwid", name="uq_user_devices_user_hwid"),
            sa.UniqueConstraint("device_uuid", name="uq_user_devices_device_uuid"),
        )
        op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"])
        op.create_index(
            "ix_user_devices_user_priority",
            "user_devices",
            ["user_id", "bi_priority", "first_seen_at"],
        )
    else:
        dev_cols = {c["name"] for c in insp.get_columns("user_devices")}
        dev_indexes = {i["name"] for i in insp.get_indexes("user_devices")}
        if "os_version" not in dev_cols:
            op.add_column("user_devices", sa.Column("os_version", sa.String(64), nullable=True))
        if "app_version" not in dev_cols:
            op.add_column("user_devices", sa.Column("app_version", sa.String(64), nullable=True))
        if "first_seen_at" not in dev_cols:
            op.add_column(
                "user_devices",
                sa.Column(
                    "first_seen_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.text("now()"),
                ),
            )
        if "ix_user_devices_user_priority" not in dev_indexes:
            op.create_index(
                "ix_user_devices_user_priority",
                "user_devices",
                ["user_id", "bi_priority", "first_seen_at"],
            )

    # 9. Webhook inbox legacy queue columns
    if "webhook_inbox" in tables:
        wi_box_cols = {c["name"] for c in insp.get_columns("webhook_inbox")}
        for col in (
            "attempts",
            "max_attempts",
            "next_attempt_at",
            "locked_at",
            "locked_by",
            "last_error_code",
            "last_error",
        ):
            if col in wi_box_cols:
                op.drop_column("webhook_inbox", col)

    # 10. BroadcastProgress: keep NOT NULL constraints, widen target_audience to 64 and media_id to 500
    if "broadcast_progress" in tables:
        bp_cols = {c["name"]: c for c in insp.get_columns("broadcast_progress")}
        if "target_audience" in bp_cols:
            op.alter_column(
                "broadcast_progress",
                "target_audience",
                type_=sa.String(64),
                existing_nullable=False,
                nullable=False,
            )
        if "media_id" in bp_cols:
            op.alter_column(
                "broadcast_progress",
                "media_id",
                type_=sa.String(500),
                existing_nullable=True,
                nullable=True,
            )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from 0045_v2_clean_unified is not supported; restore from pg_dump backup."
    )
