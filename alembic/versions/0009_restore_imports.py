"""三份匯入資料改為啟動後保留：恢復舊版在最後一次啟動時停用的那批

Revision ID: 0009_restore_imports
Revises: 0008_ai_summaries
Create Date: 2026-11-13 18:00:00
"""
from alembic import op


revision = '0009_restore_imports'
down_revision = '0008_ai_summaries'
branch_labels = None
depends_on = None

TABLES = ('mapping_versions', 'teacher_imports', 'funding_imports')


def upgrade() -> None:
    # 舊版每次啟動都把「目前資料」一起標上同一個失效時間；最晚的那個時間就是上次使用中的資料。
    # 只有在表內沒有任何目前資料時才恢復，更早被停用的版本維持停用。
    for table in TABLES:
        op.execute(
            f"UPDATE {table} SET retired_at = NULL "
            f"WHERE retired_at = (SELECT MAX(retired_at) FROM {table}) "
            f"AND NOT EXISTS (SELECT 1 FROM {table} WHERE retired_at IS NULL)"
        )


def downgrade() -> None:
    pass
