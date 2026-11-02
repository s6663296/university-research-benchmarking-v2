"""api quota

Revision ID: 0002_api_quota
Revises: 0001_initial
Create Date: 2026-10-30 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_api_quota'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('api_quota',
    sa.Column('api', sa.String(), nullable=False),
    sa.Column('limit', sa.Integer(), nullable=True),
    sa.Column('remaining', sa.Integer(), nullable=True),
    sa.Column('reset_at', sa.DateTime(), nullable=True),
    sa.Column('observed_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('api')
    )


def downgrade() -> None:
    op.drop_table('api_quota')
