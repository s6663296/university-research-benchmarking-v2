"""teacher detail（分析保存教師數的教育部對應快照，供逐格來源說明）

Revision ID: 0005_teacher_detail
Revises: 0004_mapping_recheck
Create Date: 2026-10-31 14:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_teacher_detail'
down_revision = '0004_mapping_recheck'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('run_units') as batch:
        batch.add_column(sa.Column('teacher_detail_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('run_units') as batch:
        batch.drop_column('teacher_detail_json')
