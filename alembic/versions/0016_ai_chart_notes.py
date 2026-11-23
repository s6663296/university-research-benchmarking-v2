"""AI 圖表分析：網頁 AI 總結分頁的圖表說明與重點

Revision ID: 0016_ai_chart_notes
Revises: 0015_per_faculty_no_years
Create Date: 2026-11-23 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0016_ai_chart_notes'
down_revision = '0015_per_faculty_no_years'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_chart_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('runs.id'), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('baseline_run_id', sa.Integer(), nullable=True),
        sa.Column('notes_json', sa.Text(), nullable=False),
        sa.Column('input_hash', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('edited', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('run_id', 'key'),
    )
    op.create_index('ix_ai_chart_notes_run_id', 'ai_chart_notes', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_chart_notes_run_id', 'ai_chart_notes')
    op.drop_table('ai_chart_notes')
