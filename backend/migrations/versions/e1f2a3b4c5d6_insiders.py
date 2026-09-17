"""insiders: form 4 transactions, sec filings, instrument cik

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-17 12:59:36.125765

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'sec_filings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('form', sa.String(length=10), nullable=False),
        sa.Column('filed_at', sa.Date(), nullable=False),
        sa.Column('period', sa.Date(), nullable=True),
        sa.Column('items', sa.JSON(), nullable=True),
        sa.Column('accession', sa.String(length=24), nullable=False),
        sa.Column('primary_document', sa.String(length=160), nullable=True),
        sa.Column('url', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('instrument_id', 'accession'),
    )
    with op.batch_alter_table('sec_filings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sec_filings_filed_at'), ['filed_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_sec_filings_instrument_id'), ['instrument_id'], unique=False)

    op.create_table(
        'insider_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('disclosure_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('insider_cik', sa.String(length=10), nullable=False),
        sa.Column('insider_name', sa.String(length=160), nullable=False),
        sa.Column('roles', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('filed_at', sa.DateTime(), nullable=False),
        sa.Column('code', sa.String(length=2), nullable=False),
        sa.Column('acquired', sa.Boolean(), nullable=False),
        sa.Column('shares', sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column('price', sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column('post_shares', sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column('ownership', sa.String(length=1), nullable=False),
        sa.Column('derivative', sa.Boolean(), nullable=False),
        sa.Column('is_superseded', sa.Boolean(), nullable=False),
        sa.Column('confidence', sa.String(length=16), nullable=False, server_default='EXACT'),
        sa.Column('row_hash', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['disclosure_id'], ['disclosures.id']),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('row_hash'),
    )
    with op.batch_alter_table('insider_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_insider_transactions_disclosure_id'), ['disclosure_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_insider_transactions_insider_cik'), ['insider_cik'], unique=False)
        batch_op.create_index('ix_insider_transactions_instrument_date', ['instrument_id', 'transaction_date'], unique=False)

    with op.batch_alter_table('instruments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sec_cik', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('sec_form4_fetched_at', sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f('ix_instruments_sec_cik'), ['sec_cik'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('instruments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_instruments_sec_cik'))
        batch_op.drop_column('sec_form4_fetched_at')
        batch_op.drop_column('sec_cik')

    with op.batch_alter_table('insider_transactions', schema=None) as batch_op:
        batch_op.drop_index('ix_insider_transactions_instrument_date')
        batch_op.drop_index(batch_op.f('ix_insider_transactions_insider_cik'))
        batch_op.drop_index(batch_op.f('ix_insider_transactions_disclosure_id'))
    op.drop_table('insider_transactions')

    with op.batch_alter_table('sec_filings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sec_filings_instrument_id'))
        batch_op.drop_index(batch_op.f('ix_sec_filings_filed_at'))
    op.drop_table('sec_filings')
