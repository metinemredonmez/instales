"""waitlist

Revision ID: b2d3e4f5a6c7
Revises: a1c2e3f4b5d6
Create Date: 2026-09-16 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d3e4f5a6c7'
down_revision: Union[str, Sequence[str], None] = 'a1c2e3f4b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('waitlist',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=True),
    sa.Column('lang', sa.String(length=2), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    with op.batch_alter_table('waitlist', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_waitlist_email'), ['email'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('waitlist', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_waitlist_email'))
    op.drop_table('waitlist')
