"""kap insiders: insider_transactions.kap_disclosure_index, party_kind, post_pct_stake

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-17 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('insider_transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kap_disclosure_index', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('party_kind', sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column('post_pct_stake', sa.Numeric(precision=9, scale=4), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('insider_transactions', schema=None) as batch_op:
        batch_op.drop_column('post_pct_stake')
        batch_op.drop_column('party_kind')
        batch_op.drop_column('kap_disclosure_index')
