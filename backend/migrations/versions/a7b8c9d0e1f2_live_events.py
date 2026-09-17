"""live events

Revision ID: a7b8c9d0e1f2
Revises: f44d7a1c9f2c
Create Date: 2026-09-17 01:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f44d7a1c9f2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('live_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('market_code', sa.String(length=2), nullable=True),
    sa.Column('owner_id', sa.String(length=64), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('live_events', schema=None) as batch_op:
        batch_op.create_index('ix_live_events_created', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('live_events', schema=None) as batch_op:
        batch_op.drop_index('ix_live_events_created')

    op.drop_table('live_events')
