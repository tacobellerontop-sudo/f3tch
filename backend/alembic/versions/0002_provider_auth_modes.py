"""provider authentication modes

Adds the preferred authentication mode of each provider and the result of the
last check of its stored account.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'providers',
        sa.Column('auth_mode', sa.String(length=16), nullable=False, server_default='auto'),
    )
    op.add_column('provider_credentials', sa.Column('valid', sa.Boolean(), nullable=True))
    op.add_column(
        'provider_credentials',
        sa.Column('validation_message', sa.Text(), nullable=False, server_default=''),
    )
    op.add_column('provider_credentials', sa.Column('validated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('provider_credentials') as batch:
        batch.drop_column('validated_at')
        batch.drop_column('validation_message')
        batch.drop_column('valid')
    with op.batch_alter_table('providers') as batch:
        batch.drop_column('auth_mode')
