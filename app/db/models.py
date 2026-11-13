"""SQLite 資料表（設計 §11）。JSON 內容一律以文字欄位存放，欄位名稱以 _json 結尾。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class DecimalText(TypeDecorator):
    """以字串保存 Decimal，避免 SQLite 轉成 float 失去精度。"""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(Decimal(value))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


class MappingVersion(Base):
    __tablename__ = "mapping_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    imported_at: Mapped[datetime]
    note: Mapped[str | None] = mapped_column(Text)
    # 原始檔與檢查時的規則指紋：規則改了才用原始檔重新檢查（設計 §5.2）；content 很大，不隨查詢載入
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    rules_hash: Mapped[str | None] = mapped_column(String)
    retired_at: Mapped[datetime | None]  # 啟動程式時寫入：不再是目前資料，只供在此之前建立的分析使用


class Unit(Base):
    __tablename__ = "units"
    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_version_id: Mapped[int] = mapped_column(ForeignKey("mapping_versions.id"), index=True)
    school: Mapped[str] = mapped_column(String)
    college: Mapped[str | None] = mapped_column(String)
    dept_zh: Mapped[str | None] = mapped_column(String)
    dept_en: Mapped[str] = mapped_column(String)
    ncu_college: Mapped[str | None] = mapped_column(String)
    ncu_dept: Mapped[str] = mapped_column(String)
    remark: Mapped[str | None] = mapped_column(Text)


class MappingIssue(Base):
    __tablename__ = "mapping_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_version_id: Mapped[int] = mapped_column(ForeignKey("mapping_versions.id"), index=True)
    sheet: Mapped[str] = mapped_column(String)
    row: Mapped[int | None] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)


class TeacherImport(Base):
    __tablename__ = "teacher_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_filename: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    imported_at: Mapped[datetime]
    years_json: Mapped[str] = mapped_column(Text, default="[]")
    retired_at: Mapped[datetime | None]  # 啟動程式時寫入：不再是目前資料，只供在此之前建立的分析使用


class TeacherCountRow(Base):
    __tablename__ = "teacher_counts"
    __table_args__ = (Index("ix_teacher_counts_lookup", "teacher_import_id", "moe_school", "academic_year"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_import_id: Mapped[int] = mapped_column(ForeignKey("teacher_imports.id"))
    academic_year: Mapped[int]
    moe_school: Mapped[str] = mapped_column(String)
    moe_unit_code: Mapped[str] = mapped_column(String)
    moe_unit_name: Mapped[str] = mapped_column(String)
    total: Mapped[int]
    asst_prof_plus: Mapped[int]


class UnitTeacherLink(Base):
    __tablename__ = "unit_teacher_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    school: Mapped[str] = mapped_column(String, index=True)
    dept_zh: Mapped[str] = mapped_column(String)
    moe_unit_code: Mapped[str] = mapped_column(String)
    matched_by: Mapped[str] = mapped_column(String)


class TeacherOverride(Base):
    __tablename__ = "teacher_overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    school: Mapped[str] = mapped_column(String)
    dept_zh: Mapped[str] = mapped_column(String)
    academic_year: Mapped[int]
    count: Mapped[int]
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    period_from: Mapped[int]
    period_to: Mapped[int]
    scope_json: Mapped[str] = mapped_column(Text)
    mapping_version_id: Mapped[int] = mapped_column(ForeignKey("mapping_versions.id"))
    teacher_import_id: Mapped[int | None] = mapped_column(ForeignKey("teacher_imports.id"))
    academic_year: Mapped[int]
    teacher_column: Mapped[str] = mapped_column(String)
    self_citation_strategy: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    notes_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    # 這次分析（含接續、重抓）各 API 消耗的請求數，如 {"scopus": 12, "abstract": 0, "scival": 30}；舊分析為 {}
    api_usage_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")


class RunUnit(Base):
    __tablename__ = "run_units"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    role: Mapped[str] = mapped_column(String)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="pending")
    fetched_at: Mapped[datetime | None]
    eid_count: Mapped[int | None] = mapped_column(Integer)
    teacher_count: Mapped[int | None] = mapped_column(Integer)
    teacher_source: Mapped[str] = mapped_column(Text, default="missing")
    # 建立分析時的教師數來源快照（教育部各列、人工修改）；舊分析為 None
    teacher_detail_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class RunPublication(Base):
    __tablename__ = "run_publications"
    __table_args__ = (Index("ix_run_publications_run_unit", "run_id", "unit_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    scopus_id: Mapped[str] = mapped_column(String)
    eid: Mapped[str] = mapped_column(String)
    metrics_json: Mapped[str | None] = mapped_column(Text)


# 參考文獻快取的分類名稱（只存 Scopus ID 清單）：永久有效，重設系統時保留，設定頁另有按鈕清除
REFS_ENDPOINT = "/content/abstract/refs"


class ApiCache(Base):
    __tablename__ = "api_cache"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String)
    fetched_at: Mapped[datetime]
    response_json: Mapped[str] = mapped_column(Text)


class ApiQuota(Base):
    """各 API 最近一次回應標頭中的額度（X-RateLimit-*）。"""
    __tablename__ = "api_quota"
    api: Mapped[str] = mapped_column(String, primary_key=True)
    limit: Mapped[int | None]
    remaining: Mapped[int | None]
    reset_at: Mapped[datetime | None]
    observed_at: Mapped[datetime]


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (Index("ix_results_run", "run_id", "comparison", "indicator_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    comparison: Mapped[str] = mapped_column(String)
    indicator_id: Mapped[str] = mapped_column(String)
    value: Mapped[Decimal | None] = mapped_column(DecimalText)
    raw: Mapped[Decimal | None] = mapped_column(DecimalText)
    five_year_avg: Mapped[Decimal | None] = mapped_column(DecimalText)
    rank: Mapped[int | None] = mapped_column(Integer)


class Anomaly(Base):
    __tablename__ = "anomalies"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("units.id"))
    kind: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text)


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)


class FundingImport(Base):
    """計畫經費（經分表）匯入：研發處計畫管理系統匯出的「計畫基本資料」xlsx。"""
    __tablename__ = "funding_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    imported_at: Mapped[datetime]
    generated_at: Mapped[str | None] = mapped_column(String)   # 檔案標題列的「報表產製日期時間」
    years_json: Mapped[str] = mapped_column(Text, default="[]")  # 檔案內的年度（民國年）
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    retired_at: Mapped[datetime | None]  # 啟動程式時寫入：不再是目前資料，只供在此之前建立的分析使用


class FundingProject(Base):
    __tablename__ = "funding_projects"
    __table_args__ = (Index("ix_funding_projects_import_year", "funding_import_id", "year"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    funding_import_id: Mapped[int] = mapped_column(ForeignKey("funding_imports.id"))
    row: Mapped[int] = mapped_column(Integer)                  # Excel 列號，方便回頭核對
    year: Mapped[int] = mapped_column(Integer)                 # 年度（民國年）
    main_category: Mapped[str | None] = mapped_column(String)  # 計畫主類，例如 國科會計畫、產學合作計畫
    category: Mapped[str | None] = mapped_column(String)       # 計畫類別
    serial: Mapped[str | None] = mapped_column(String)         # 計畫流水號
    nstc_no: Mapped[str | None] = mapped_column(String)        # 國科會計畫編號
    title: Mapped[str | None] = mapped_column(Text)
    pi: Mapped[str | None] = mapped_column(String)
    pi_college: Mapped[str | None] = mapped_column(String)     # 計畫主持人主聘單位一級
    pi_dept: Mapped[str | None] = mapped_column(String)        # 計畫主持人主聘單位二級
    exec_college: Mapped[str | None] = mapped_column(String)   # 計畫執行單位一級名稱
    exec_dept: Mapped[str | None] = mapped_column(String)      # 計畫執行單位二級名稱
    sponsor: Mapped[str | None] = mapped_column(String)        # 委託單位名稱
    amount: Mapped[Decimal] = mapped_column(DecimalText)       # 經費總額
