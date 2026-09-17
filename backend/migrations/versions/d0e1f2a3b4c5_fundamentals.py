"""fundamentals: statements, metric snapshots, instrument shares outstanding

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-17 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'fundamentals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('period_kind', sa.String(length=9), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instrument_id', 'kind', 'period_kind', 'period_end'),
    )
    with op.batch_alter_table('fundamentals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fundamentals_instrument_id'), ['instrument_id'], unique=False)

    op.create_table(
        'fundamental_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('quote_currency', sa.String(length=3), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instrument_id', 'as_of'),
    )
    with op.batch_alter_table('fundamental_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fundamental_snapshots_instrument_id'), ['instrument_id'], unique=False)

    with op.batch_alter_table('instruments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shares_outstanding', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('shares_as_of', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('instruments', schema=None) as batch_op:
        batch_op.drop_column('shares_as_of')
        batch_op.drop_column('shares_outstanding')

    with op.batch_alter_table('fundamental_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fundamental_snapshots_instrument_id'))
    op.drop_table('fundamental_snapshots')

    with op.batch_alter_table('fundamentals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fundamentals_instrument_id'))
    op.drop_table('fundamentals')
