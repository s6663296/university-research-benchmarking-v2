"""分析加上隨機唯一鍵 uid（編號會重排，uid 不變）

Revision ID: 0012_run_uid
Revises: 0011_ai_two_columns
Create Date: 2026-11-21 23:00:00
"""
import secrets

from alembic import op
import sqlalchemy as sa


revision = '0012_run_uid'
down_revision = '0011_ai_two_columns'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('uid', sa.String(), nullable=True))
    conn = op.get_bind()
    for (run_id,) in conn.execute(sa.text("SELECT id FROM runs")).all():
        conn.execute(sa.text("UPDATE runs SET uid = :uid WHERE id = :id"), {"uid": secrets.token_hex(4), "id": run_id})
    # 不用 batch_alter_table 改成 NOT NULL：SQLite 要重建 runs 表，子表有外鍵指向它，DROP TABLE 會失敗。
    # 新分析由程式（Run.uid 的 default）產生 uid。
    op.create_index('ix_runs_uid', 'runs', ['uid'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_runs_uid', table_name='runs')
    with op.batch_alter_table('runs') as batch:
        batch.drop_column('uid')
