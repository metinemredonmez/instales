"""market_prices: open/high/low + source

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-17 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Every existing row was printed by the Yahoo prototype, hence the server default.
    with op.batch_alter_table('market_prices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('open', sa.Numeric(precision=20, scale=4), nullable=True))
        batch_op.add_column(sa.Column('high', sa.Numeric(precision=20, scale=4), nullable=True))
        batch_op.add_column(sa.Column('low', sa.Numeric(precision=20, scale=4), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=16), nullable=False, server_default='yahoo'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('market_prices', schema=None) as batch_op:
        batch_op.drop_column('source')
        batch_op.drop_column('low')
        batch_op.drop_column('high')
        batch_op.drop_column('open')
