"""測試用：建立可執行的分析（mapping + 教師數 + run）與模擬 Elsevier 伺服器。"""

import re
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.elsevier.client import ElsevierClient
from app.elsevier.inset_selfcite import REF_CHECK
from app.mapping.service import import_mapping
from app.runs.scope import RunConfig
from app.runs.service import create_run
from app.teachers.moe import import_moe
from app.teachers.service import auto_link
from tests.elsevier_fixtures import (ABSTRACT_URL, OPENALEX_URL, SCIVAL_URL, SCOPUS_URL, abstract_full, ids67,
                                     scival_handler)
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
    imp = import_moe(session, "moe.xlsx", sample_moe())
    auto_link(session, v.id, imp.id)


def doi_of(sid: str) -> str:
    return f"10.9999/{sid}"


@dataclass
class FakeElsevier:
    """依查詢字串回傳論文；fail 中的關鍵字回傳指定的 HTTP 狀態；refs 是論文 → 它引用的 Scopus ID。

    查詢中有 REF(<Scopus ID>) 時，只回傳參考文獻有其中任一篇的論文；ref_search=False 時回 HTTP 400。
    OpenAlex 依 refs 回答：oa_missing 中的論文查不到，oa_drop 中的（引用者, 被引者）缺漏。
    abstract_quota：Abstract Retrieval 只回應這麼多次，之後回 429 額度用完。
    """
    papers: dict = field(default_factory=lambda: dict(PAPERS))
    fail: dict = field(default_factory=dict)
    refs: dict = field(default_factory=dict)
    ref_search: bool = True
    oa_missing: set = field(default_factory=set)
    oa_drop: set = field(default_factory=set)
    abstract_quota: int | None = None
    scopus_calls: list = field(default_factory=list)
    abstract_calls: list = field(default_factory=list)
    openalex_calls: list = field(default_factory=list)

    def all_refs(self) -> dict:
        cited, citing = REF_CHECK   # 檢查 REF 是否可用時查的已知引用關係
        return {citing: [cited], **self.refs}

    def _cites(self, sid: str, targets: list[str]) -> bool:
        return bool(set(self.all_refs().get(sid, [])) & set(targets))

    @staticmethod
    def _found(ids: list[str]) -> httpx.Response:
        entries = [{"dc:identifier": f"SCOPUS_ID:{i}", "eid": f"2-s2.0-{i}", "prism:doi": doi_of(i)} for i in ids] \
            or [{"error": "Result set was empty"}]
        return httpx.Response(200, json={"search-results": {
            "opensearch:totalResults": str(len(ids)), "cursor": {"@next": "END"}, "entry": entries}})

    def scopus(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        self.scopus_calls.append(query)
        cited = re.findall(r"REF\((\d+)\)", query)
        if cited and not self.ref_search:
            return httpx.Response(400, json={"service-error": {"status": {"statusCode": "INVALID_INPUT"}}})
        match = re.search(r"AFFIL\(([^)]+)\)", query)
        if match is None and cited:   # REF 檢查：REF(...) AND EID(...)
            return self._found([i for i in re.findall(r"EID\(2-s2\.0-(\d+)\)", query) if self._cites(i, cited)])
        if match is None:  # 連線檢查
            return httpx.Response(200, json={"search-results": {"opensearch:totalResults": "1", "entry": []}})
        name = re.sub(r'"\s*W/\d+\s*"', ", ", match.group(1)).strip('"')  # 還原成「系所, 校名」
        for key, status in self.fail.items():
            if key in name:
                return httpx.Response(status)
        if request.url.params.get("cursor") == "END":
            return httpx.Response(200, json={"search-results": {"opensearch:totalResults": "0", "entry": []}})
        ids = next((ids67()[s] for k, s in self.papers.items() if k in name), [])
        return self._found([i for i in ids if self._cites(i, cited)] if cited else ids)

    def abstract(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.path.rsplit("/", 1)[-1]
        if self.abstract_quota is not None and len(self.abstract_calls) >= self.abstract_quota:
            return httpx.Response(429, headers={"X-RateLimit-Remaining": "0",
                                                "X-RateLimit-Reset": str(int(time.time()) + 7 * 86400)})
        self.abstract_calls.append(sid)
        return httpx.Response(200, json=abstract_full(self.refs.get(sid, [])))

    def openalex(self, request: httpx.Request) -> httpx.Response:
        dois = request.url.params["filter"].removeprefix("doi:").split("|")
        self.openalex_calls.append(dois)
        refs = self.all_refs()
        works = [{"id": f"https://openalex.org/W{sid}", "doi": f"https://doi.org/{d}",
                  "referenced_works": [f"https://openalex.org/W{r}" for r in refs.get(sid, [])
                                       if (sid, r) not in self.oa_drop]}
                 for d in dois if (sid := d.rsplit("/", 1)[-1]) not in self.oa_missing]
        return httpx.Response(200, json={"results": works})

    def install(self, respx_mock) -> None:
        respx_mock.get(url__regex=SCOPUS_URL).mock(side_effect=self.scopus)
        respx_mock.get(url__regex=ABSTRACT_URL).mock(side_effect=self.abstract)
        respx_mock.get(url__regex=OPENALEX_URL).mock(side_effect=self.openalex)
        respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)


def fake_client() -> ElsevierClient:
    return ElsevierClient("k", backoff_base=0, rate_per_sec=1000, max_retries=0)


def make_run(session, **kw):
    seed(session)
    return create_run(session, default_config(**kw), today=TODAY)
