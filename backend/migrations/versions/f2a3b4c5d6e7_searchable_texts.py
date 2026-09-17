"""searchable_texts: full-text search index over disclosures, filings, news and AI notes

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-17 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'searchable_texts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('ref_id', sa.Integer(), nullable=False),
        sa.Column('market_code', sa.String(length=2), nullable=False),
        sa.Column('symbols', sa.JSON(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('url', sa.String(length=512), nullable=True),
        sa.Column('link', sa.String(length=128), nullable=True),
        sa.Column('superseded', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kind', 'ref_id', name='uq_searchable_texts_ref'),
    )
    with op.batch_alter_table('searchable_texts', schema=None) as batch_op:
        batch_op.create_index('ix_searchable_texts_market_date', ['market_code', 'date'], unique=False)

    # Postgres carries the search vector itself: a stored generated column over title + body in the 'simple'
    # configuration (no stemming, so Turkish words match as typed) with a GIN index. The title is weighted A and
    # the body D, so ts_rank_cd (default weights 0.1/0.2/0.4/1.0) puts the document *about* the query ahead of a
    # portfolio report that mentions it once — the same order the SQLite ranker in services/search gives. SQLite
    # (dev, tests) gets the plain table and services/search falls back to LIKE; the ORM model maps neither column
    # nor index.
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(
            "ALTER TABLE searchable_texts ADD COLUMN tsv tsvector GENERATED ALWAYS AS "
            "(setweight(to_tsvector('simple', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('simple', coalesce(body, '')), 'D')) STORED"
        )
        op.execute('CREATE INDEX ix_searchable_texts_tsv ON searchable_texts USING GIN (tsv)')


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_searchable_texts_tsv')  # the generated column goes with the table
    with op.batch_alter_table('searchable_texts', schema=None) as batch_op:
        batch_op.drop_index('ix_searchable_texts_market_date')
    op.drop_table('searchable_texts')
