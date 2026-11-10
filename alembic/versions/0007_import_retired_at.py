"""匯入資料的失效時間：每次啟動程式後，舊匯入不再是目前資料

Revision ID: 0007_import_retired_at
Revises: 0006_funding
Create Date: 2026-11-10 22:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007_import_retired_at'
down_revision = '0006_funding'
branch_labels = None
depends_on = None

TABLES = ('mapping_versions', 'teacher_imports', 'funding_imports')


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('retired_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('retired_at')
