"""每次分析消耗的 API 額度

Revision ID: 0010_run_api_usage
Revises: 0009_restore_imports
Create Date: 2026-11-13 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0010_run_api_usage'
down_revision = '0009_restore_imports'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 開發中曾編為 0008_run_api_usage，已經加過欄位的資料庫會重跑這一步
    if 'api_usage_json' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('runs')}:
        return
    op.add_column('runs', sa.Column('api_usage_json', sa.Text(), nullable=False, server_default='{}'))


def downgrade() -> None:
    with op.batch_alter_table('runs') as batch:
        batch.drop_column('api_usage_json')
