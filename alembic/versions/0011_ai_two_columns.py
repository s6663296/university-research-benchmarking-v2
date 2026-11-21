"""AI 總結改成兩欄：挑戰／機會、競爭學校變化

Revision ID: 0011_ai_two_columns
Revises: 0010_run_api_usage
Create Date: 2026-11-21 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011_ai_two_columns'
down_revision = '0010_run_api_usage'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ai_summaries', sa.Column('challenges', sa.Text(), nullable=True))
    op.add_column('ai_summaries', sa.Column('rival_changes', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ai_summaries') as batch:
        batch.drop_column('rival_changes')
        batch.drop_column('challenges')
