"""每次分析消耗的 API 額度

Revision ID: 0008_run_api_usage
Revises: 0007_import_retired_at
Create Date: 2026-11-13 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008_run_api_usage'
down_revision = '0007_import_retired_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('api_usage_json', sa.Text(), nullable=False, server_default='{}'))


def downgrade() -> None:
    with op.batch_alter_table('runs') as batch:
        batch.drop_column('api_usage_json')
