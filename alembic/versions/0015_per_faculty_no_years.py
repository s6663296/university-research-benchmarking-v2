"""師均改成期間總篇數 ÷ 單年教師數（不再除以年數），拿掉「年平均」欄

舊分析的結果要依新公式重算：這裡只留下標記，程式啟動時（app.runs.executor.recompute_flagged）
用抓到的原始資料逐次重算，算完清掉標記。

Revision ID: 0015_per_faculty_no_years
Revises: 0014_school_affiliations
Create Date: 2026-11-23 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0015_per_faculty_no_years'
down_revision = '0014_school_affiliations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('results') as batch:
        batch.drop_column('five_year_avg')
    op.execute("INSERT OR REPLACE INTO app_settings (key, value_json) VALUES ('recompute_results', 'true')")


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'recompute_results'")
    with op.batch_alter_table('results') as batch:
        batch.add_column(sa.Column('five_year_avg', sa.String(), nullable=True))
