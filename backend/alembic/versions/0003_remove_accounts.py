"""remove accounts

FETCH became a local application without sign-in. Sessions, invitations and
per-user quotas are gone. The ``users`` table stays: its first administrator
becomes the one local profile that owns the downloads and files.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('sessions')
    op.drop_table('invitations')
    op.drop_table('user_quotas')
    # Passwords are of no use any more; do not keep their hashes around.
    op.execute("UPDATE users SET password_hash = ''")


def downgrade() -> None:
    op.create_table('user_quotas',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('max_concurrent', sa.Integer(), nullable=False),
    sa.Column('max_storage_bytes', sa.BigInteger(), nullable=False),
    sa.Column('max_file_bytes', sa.BigInteger(), nullable=False),
    sa.Column('daily_downloads', sa.Integer(), nullable=False),
    sa.Column('bandwidth_bps', sa.BigInteger(), nullable=False),
    sa.Column('max_queued', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('invitations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('used_by_id', sa.Integer(), nullable=True),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['used_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_invitations_token_hash', 'invitations', ['token_hash'], unique=True)
    op.create_table('sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=256), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'], unique=False)
    op.create_index('ix_sessions_token_hash', 'sessions', ['token_hash'], unique=True)
    op.create_index('ix_sessions_expires_at', 'sessions', ['expires_at'], unique=False)
