"""測試用：建立可執行的分析（mapping + 教師數 + run）與模擬 Elsevier 伺服器。"""

import re
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.elsevier.client import ElsevierClient
from app.mapping.service import import_mapping
from app.runs.scope import RunConfig
from app.runs.service import create_run
from app.teachers.moe import import_moe
from app.teachers.service import auto_link
from tests.elsevier_fixtures import SCIVAL_URL, SCOPUS_URL, ids67, scival_handler
from tests.mapping_builder import sample_mapping
from tests.moe_builder import sample_moe

TODAY = date(2026, 10, 30)

# 英文名稱中的關鍵字 → 該單位的論文（取自錄下的 67 篇）
PAPERS = {
    "Information Management, National Central": slice(0, 20),
    "Information Management, National Taiwan": slice(20, 40),
    "Management Information Systems, National Chengchi": slice(40, 50),
    "Finance, National Central": slice(50, 60),
    "Accounting, National Central": slice(60, 67),
    "Finance, National Taiwan": slice(0, 0),
}


def default_config(**kw) -> RunConfig:
    base = dict(year_from=2021, year_to=2025, ncu_depts=("資訊管理學系",),
                comparisons=frozenset({"benchmark", "college"}), schools=("台大", "政治"),
                custom_unit_ids=(), academic_year=114, teacher_column="total")
    base.update(kw)
    return RunConfig(**base)


def seed(session) -> None:
    v, _ = import_mapping(session, "m.xlsx", sample_mapping())
    imp, _ = import_moe(session, "moe.xlsx", sample_moe())
    auto_link(session, v.id, imp.id)


@dataclass
class FakeElsevier:
    """依查詢字串回傳論文；fail 中的關鍵字回傳指定的 HTTP 狀態。"""
    papers: dict = field(default_factory=lambda: dict(PAPERS))
    fail: dict = field(default_factory=dict)
    scopus_calls: list = field(default_factory=list)

    def scopus(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        self.scopus_calls.append(query)
        match = re.search(r'AFFIL\("([^"]+)"\)', query)
        if match is None:  # 連線檢查
            return httpx.Response(200, json={"search-results": {"opensearch:totalResults": "1", "entry": []}})
        name = match.group(1)
        for key, status in self.fail.items():
            if key in name:
                return httpx.Response(status)
        if request.url.params.get("cursor") == "END":
            return httpx.Response(200, json={"search-results": {"opensearch:totalResults": "0", "entry": []}})
        ids = next((ids67()[s] for k, s in self.papers.items() if k in name), [])
        entries = [{"dc:identifier": f"SCOPUS_ID:{i}", "eid": f"2-s2.0-{i}"} for i in ids] or \
                  [{"error": "Result set was empty"}]
        return httpx.Response(200, json={"search-results": {
            "opensearch:totalResults": str(len(ids)), "cursor": {"@next": "END"}, "entry": entries}})

    def install(self, respx_mock) -> None:
        respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=self.scopus)
        respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)


def fake_client() -> ElsevierClient:
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000, max_retries=0)


def make_run(session, **kw):
    seed(session)
    return create_run(session, default_config(**kw), today=TODAY)
