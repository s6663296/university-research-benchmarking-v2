"""drop school aliases（教育部校名改為內建對照，報告直接用 mapping 的名稱）

Revision ID: 0003_drop_school_aliases
Revises: 0002_api_quota
Create Date: 2026-10-30 19:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_drop_school_aliases'
down_revision = '0002_api_quota'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('school_aliases')
    op.execute("DELETE FROM app_settings WHERE key IN ('dept_short_names', 'college_short_names')")


def downgrade() -> None:
    op.create_table('school_aliases',
    sa.Column('sheet_name', sa.String(), nullable=False),
    sa.Column('moe_name', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('sheet_name')
    )
