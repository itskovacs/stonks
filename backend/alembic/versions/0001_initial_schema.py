"""Initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-04-30

"""
import sqlalchemy as sa
import sqlmodel.sql.sqltypes

from alembic import op

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user',
        sa.Column('username', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
        sa.Column('hashed_password', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('username', name=op.f('pk_user'))
    )

    op.create_table('envelope',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column('color', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('cash_available', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user'], ['user.username'], name=op.f('fk_envelope_user_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_envelope')),
        sa.UniqueConstraint('user', 'name', name='uq_envelope_user_name')
    )
    with op.batch_alter_table('envelope', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_envelope_user'), ['user'], unique=False)

    op.create_table('watchlist_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('ticker', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column('added_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user'], ['user.username'], name=op.f('fk_watchlist_item_user_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_watchlist_item')),
        sa.UniqueConstraint('user', 'ticker', name='uq_watchlist_item_user_ticker')
    )
    with op.batch_alter_table('watchlist_item', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_watchlist_item_user'), ['user'], unique=False)

    op.create_table('transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('envelope_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('type', sa.Enum('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW', 'DIVIDEND', name='transactiontype'), nullable=False),
        sa.Column('ticker', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column('shares', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('fees', sa.Float(), nullable=False),
        sa.Column('total', sa.Float(), nullable=False),
        sa.Column('note', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.ForeignKeyConstraint(['envelope_id'], ['envelope.id'], name=op.f('fk_transaction_envelope_id_envelope'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user'], ['user.username'], name=op.f('fk_transaction_user_user'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_transaction'))
    )
    with op.batch_alter_table('transaction', schema=None) as batch_op:
        batch_op.create_index('idx_transaction_user_date', ['user', 'date'], unique=False)
        batch_op.create_index(batch_op.f('ix_transaction_date'), ['date'], unique=False)
        batch_op.create_index(batch_op.f('ix_transaction_envelope_id'), ['envelope_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_transaction_user'), ['user'], unique=False)


def downgrade():
    with op.batch_alter_table('transaction', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_transaction_user'))
        batch_op.drop_index(batch_op.f('ix_transaction_envelope_id'))
        batch_op.drop_index(batch_op.f('ix_transaction_date'))
        batch_op.drop_index('idx_transaction_user_date')

    op.drop_table('transaction')
    with op.batch_alter_table('watchlist_item', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_watchlist_item_user'))

    op.drop_table('watchlist_item')
    with op.batch_alter_table('envelope', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_envelope_user'))

    op.drop_table('envelope')
    op.drop_table('user')
