"""user + ai note language

Revision ID: a1c2e3f4b5d6
Revises: fd2dcf836a1f
Create Date: 2026-09-16 15:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, Sequence[str], None] = 'fd2dcf836a1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The original unique constraint on ai_notes was unnamed; Postgres auto-named it, SQLite needs a
# naming convention so batch mode can reflect and drop it.
PG_OLD_UQ = "ai_notes_kind_market_code_subject_as_of_key"
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lang', sa.String(length=2), nullable=False, server_default='tr'))

    if op.get_bind().dialect.name == "postgresql":
        op.add_column('ai_notes', sa.Column('lang', sa.String(length=2), nullable=False, server_default='tr'))
        op.drop_constraint(PG_OLD_UQ, 'ai_notes', type_='unique')
        op.create_unique_constraint('uq_ai_notes_note', 'ai_notes', ['kind', 'market_code', 'subject', 'as_of', 'lang'])
    else:
        with op.batch_alter_table('ai_notes', schema=None, naming_convention=NAMING) as batch_op:
            batch_op.add_column(sa.Column('lang', sa.String(length=2), nullable=False, server_default='tr'))
            batch_op.drop_constraint('uq_ai_notes_kind', type_='unique')
            batch_op.create_unique_constraint('uq_ai_notes_note', ['kind', 'market_code', 'subject', 'as_of', 'lang'])


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint('uq_ai_notes_note', 'ai_notes', type_='unique')
        op.create_unique_constraint(PG_OLD_UQ, 'ai_notes', ['kind', 'market_code', 'subject', 'as_of'])
        op.drop_column('ai_notes', 'lang')
    else:
        with op.batch_alter_table('ai_notes', schema=None) as batch_op:
            batch_op.drop_constraint('uq_ai_notes_note', type_='unique')
            batch_op.create_unique_constraint('uq_ai_notes_kind', ['kind', 'market_code', 'subject', 'as_of'])
            batch_op.drop_column('lang')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('lang')
