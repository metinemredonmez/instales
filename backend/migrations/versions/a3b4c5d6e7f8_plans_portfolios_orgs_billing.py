"""plans, portfolios, organisations and billing: portfolios/positions/transactions, organizations/org_members,
subscriptions, processed_webhooks; users.plan_source + users.plan_until; auth_tokens.subject

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-17 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('plan_source', sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column('plan_until', sa.Date(), nullable=True))
    with op.batch_alter_table('auth_tokens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subject', sa.String(length=64), nullable=True))

    op.create_table(
        'portfolios',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('market_code', sa.String(length=8), nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['market_code'], ['markets.code'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('portfolios', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_portfolios_owner_id'), ['owner_id'], unique=False)

    op.create_table(
        'portfolio_positions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('portfolio_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column('avg_cost', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('opened_at', sa.Date(), nullable=True),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
        sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('portfolio_id', 'instrument_id'),
    )
    with op.batch_alter_table('portfolio_positions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_portfolio_positions_portfolio_id'), ['portfolio_id'], unique=False)

    op.create_table(
        'portfolio_transactions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('portfolio_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.Integer(), nullable=False),
        sa.Column('side', sa.String(length=4), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column('price', sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column('traded_at', sa.Date(), nullable=False),
        sa.Column('fee', sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
        sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('portfolio_transactions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_portfolio_transactions_portfolio_id'), ['portfolio_id'], unique=False)

    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('plan', sa.String(length=16), nullable=False),
        sa.Column('seats', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_user_id'),
    )

    op.create_table(
        'org_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('role', sa.String(length=8), nullable=False),
        sa.Column('invited_email', sa.String(length=254), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'invited_email'),
    )
    with op.batch_alter_table('org_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_org_members_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_org_members_user_id'), ['user_id'], unique=False)

    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('org_id', sa.Integer(), nullable=True),
        sa.Column('plan', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('provider_customer_id', sa.String(length=64), nullable=True),
        sa.Column('provider_subscription_id', sa.String(length=64), nullable=True),
        sa.Column('current_period_end', sa.DateTime(), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(length=256), nullable=True),
        sa.Column('provider_event_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_subscription_id'),
    )
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscriptions_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscriptions_provider_customer_id'), ['provider_customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscriptions_provider_subscription_id'), ['provider_subscription_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscriptions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscriptions_user_id'), ['user_id'], unique=False)

    op.create_table(
        'processed_webhooks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('event_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'event_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('processed_webhooks')
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscriptions_user_id'))
        batch_op.drop_index(batch_op.f('ix_subscriptions_status'))
        batch_op.drop_index(batch_op.f('ix_subscriptions_provider_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_subscriptions_provider_customer_id'))
        batch_op.drop_index(batch_op.f('ix_subscriptions_org_id'))
    op.drop_table('subscriptions')
    with op.batch_alter_table('org_members', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_org_members_user_id'))
        batch_op.drop_index(batch_op.f('ix_org_members_org_id'))
    op.drop_table('org_members')
    op.drop_table('organizations')
    with op.batch_alter_table('portfolio_transactions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_portfolio_transactions_portfolio_id'))
    op.drop_table('portfolio_transactions')
    with op.batch_alter_table('portfolio_positions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_portfolio_positions_portfolio_id'))
    op.drop_table('portfolio_positions')
    with op.batch_alter_table('portfolios', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_portfolios_owner_id'))
    op.drop_table('portfolios')
    with op.batch_alter_table('auth_tokens', schema=None) as batch_op:
        batch_op.drop_column('subject')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('plan_until')
        batch_op.drop_column('plan_source')
