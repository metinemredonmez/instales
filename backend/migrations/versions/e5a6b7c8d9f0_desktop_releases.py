"""desktop releases

Revision ID: e5a6b7c8d9f0
Revises: d4f5a6b7c8e9
Create Date: 2026-09-16 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a6b7c8d9f0'
down_revision: Union[str, Sequence[str], None] = 'd4f5a6b7c8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('releases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=254), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version')
    )
    with op.batch_alter_table('releases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_releases_status'), ['status'], unique=False)
    op.create_table('release_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('release_id', sa.Integer(), nullable=False),
    sa.Column('platform', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('filename', sa.String(length=256), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('signature', sa.Text(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(), nullable=False),
    sa.Column('downloads', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['release_id'], ['releases.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('release_id', 'filename')
    )
    with op.batch_alter_table('release_files', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_release_files_release_id'), ['release_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('release_files', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_release_files_release_id'))
    op.drop_table('release_files')
    with op.batch_alter_table('releases', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_releases_status'))
    op.drop_table('releases')
