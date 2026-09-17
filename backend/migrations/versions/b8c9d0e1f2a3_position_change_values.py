"""position change values, weight delta, gap periods

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('position_changes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('from_value', sa.Numeric(precision=20, scale=4), nullable=True))
        batch_op.add_column(sa.Column('to_value', sa.Numeric(precision=20, scale=4), nullable=True))
        batch_op.add_column(sa.Column('delta_weight_pct', sa.Numeric(precision=9, scale=4), nullable=True))
        batch_op.add_column(sa.Column('pct_change_qty', sa.Numeric(precision=20, scale=4), nullable=True))
        batch_op.add_column(sa.Column('gap_periods', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('position_changes', schema=None) as batch_op:
        batch_op.drop_column('gap_periods')
        batch_op.drop_column('pct_change_qty')
        batch_op.drop_column('delta_weight_pct')
        batch_op.drop_column('to_value')
        batch_op.drop_column('from_value')
