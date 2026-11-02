"""mapping recheck（保存 mapping 原始檔與檢查規則指紋，首頁可依目前的規則重新檢查）

Revision ID: 0004_mapping_recheck
Revises: 0003_drop_school_aliases
Create Date: 2026-10-31 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_mapping_recheck'
down_revision = '0003_drop_school_aliases'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('mapping_versions') as batch:
        batch.add_column(sa.Column('content', sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column('rules_hash', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('mapping_versions') as batch:
        batch.drop_column('rules_hash')
        batch.drop_column('content')
