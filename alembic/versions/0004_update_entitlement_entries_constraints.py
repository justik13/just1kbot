"""Update entitlement_entries check constraints for referral bonuses.

Revision ID: 0004_update_entitlement_entries_constraints
Revises: 0003_add_server_health_fields
Create Date: 2026-08-15 08:00:00.000000
"""

from typing import Sequence, Union
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0004_update_entitlement_entries_constraints'
down_revision: Union[str, Sequence[str], None] = '0003_add_server_health_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_entitlement_entries_shape', 'entitlement_entries', type_='check')
    op.drop_constraint('ck_entitlement_entries_type', 'entitlement_entries', type_='check')

    op.create_check_constraint(
        'ck_entitlement_entries_type',
        'entitlement_entries',
        "entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'referral_reversal', 'manual_grant', 'tariff_change')",
    )
    op.create_check_constraint(
        'ck_entitlement_entries_shape',
        'entitlement_entries',
        "(((entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'manual_grant')) AND (days_delta > 0) AND (reversed_entry_id IS NULL) AND ((hours_delta IS NULL) OR (hours_delta = (days_delta * 24)))) OR ((entry_type = 'tariff_change') AND (source_type = 'quote') AND (days_delta = 0) AND (hours_delta > 0) AND (reversed_entry_id IS NULL)) OR ((entry_type = 'referral_reversal') AND (days_delta < 0) AND (reversed_entry_id IS NOT NULL) AND ((hours_delta IS NULL) OR (hours_delta = (days_delta * 24)))))",
    )


def downgrade() -> None:
    op.drop_constraint('ck_entitlement_entries_shape', 'entitlement_entries', type_='check')
    op.drop_constraint('ck_entitlement_entries_type', 'entitlement_entries', type_='check')
