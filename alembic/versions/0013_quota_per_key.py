"""額度改以（API, Key）為單位記錄，首頁可同時顯示多組 Key 的額度

Revision ID: 0013_quota_per_key
Revises: 0012_run_uid
Create Date: 2026-11-22 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0013_quota_per_key'
down_revision = '0012_run_uid'
branch_labels = None
depends_on = None

COLUMNS = "api, \"limit\", remaining, reset_at, observed_at"


def _table(name: str, *pk: str) -> None:
    op.create_table(name,
    sa.Column('api', sa.String(), nullable=False),
    *([sa.Column('key_id', sa.String(), nullable=False, server_default='')] if 'key_id' in pk else []),
    sa.Column('limit', sa.Integer(), nullable=True),
    sa.Column('remaining', sa.Integer(), nullable=True),
    sa.Column('reset_at', sa.DateTime(), nullable=True),
    sa.Column('observed_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint(*pk)
    )


def upgrade() -> None:
    # 既有紀錄的 key_id 為空字串，顯示時視為第 1 組 Key 的額度，直到下一次請求更新
    _table('api_quota_new', 'api', 'key_id')
    op.execute(f"INSERT INTO api_quota_new ({COLUMNS}) SELECT {COLUMNS} FROM api_quota")
    op.drop_table('api_quota')
    op.rename_table('api_quota_new', 'api_quota')


def downgrade() -> None:
    _table('api_quota_old', 'api')
    op.execute(f"INSERT OR IGNORE INTO api_quota_old ({COLUMNS}) SELECT {COLUMNS} FROM api_quota")
    op.drop_table('api_quota')
    op.rename_table('api_quota_old', 'api_quota')
