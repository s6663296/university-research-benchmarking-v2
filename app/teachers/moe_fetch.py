"""從教育部大專校院校務資訊公開平台直接下載「教1-1」檔並匯入。

網站流程（無需登入）：POST Export 產生檔案 → 回傳 handle → GET Download 取得 xlsx。
Download 被限流時仍回 200，內容是一段文字而不是 xlsx，所以用檔頭 PK 判斷。
"""

import asyncio
import json
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import TeacherCountRow
from app.mapping.service import latest_version
from app.teachers.moe import import_moe
from app.teachers.service import auto_link

BASE_URL = "https://udb.moe.edu.tw"
REPORT_PATH = "/udata/DetailReportList/%E6%95%99%E8%81%B7%E9%A1%9E/StatRegularTeacher"
SOURCE_URL = BASE_URL + REPORT_PATH + "/Index"
SOURCE_NAME = "教育部大專校院校務資訊公開平台：教1-1.專任教師數-以「系(所)」統計"


class MoeFetchError(Exception):
    pass


def make_http() -> httpx.AsyncClient:
    # 教育部憑證鏈缺 Subject Key Identifier，Python 3.13+ 的嚴格檢查會拒絕；仍驗證憑證，只關掉 strict 旗標
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return httpx.AsyncClient(base_url=BASE_URL, timeout=120, follow_redirects=True, verify=context,
                             headers={"Referer": SOURCE_URL, "X-Requested-With": "XMLHttpRequest"})


async def fetch_years(http: httpx.AsyncClient) -> list[int]:
    r = await http.get(REPORT_PATH + "/Index")
    r.raise_for_status()
    select = re.search(r'<select[^>]*id="ddlPeriodList".*?</select>', r.text, re.S)
    years = sorted({int(y) for y in re.findall(r"<option[^>]*>\s*(\d+)\s*</option>", select.group(0))}) if select else []
    if not years:
        raise MoeFetchError("教育部網頁中找不到學年度選項，網站可能已改版")
    return years


async def download_years(http: httpx.AsyncClient, years: list[int]) -> tuple[str, bytes]:
    parameter = json.dumps({"Year": [str(y) for y in years], "BuildType": [], "UniversityType": [],
                            "UniversityList": [], "DepartmentName": "", "PageSize": "10", "PageNumber": 1})
    r = await http.post(REPORT_PATH + "/Export", data={"FileType": "xlsx", "Parameter": parameter})
    r.raise_for_status()
    try:
        info = r.json()
    except ValueError:
        raise MoeFetchError("教育部網站沒有回傳檔案資訊，請稍後再試") from None
    if info.get("success") is False or not info.get("handle") or not info.get("fileName"):
        raise MoeFetchError(info.get("message") or "教育部網站匯出失敗，請稍後再試")
    if info.get("fileResult") is False:
        raise MoeFetchError("所選學年度沒有資料")
    r = await http.get(REPORT_PATH + "/Download/", params={"fileGuid": info["handle"], "filename": info["fileName"]})
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        message = r.text.split("request:")[0].strip(" ,，\n") or "下載內容不是 xlsx 檔"
        raise MoeFetchError(f"教育部網站回應：{message[:100]}")
    return info["fileName"], r.content


@dataclass
class FetchJob:
    years: list[int]
    status: str = "running"  # running / done / failed
    steps: list[str] = field(default_factory=list)
    error: str | None = None
    file_path: Path | None = None
    row_count: int = 0


class MoeFetcher:
    """同一時間只跑一個下載；狀態放在記憶體，供頁面輪詢。"""

    def __init__(self, session_factory: sessionmaker[Session], data_dir: Path, http_factory=make_http):
        self.session_factory = session_factory
        self.data_dir = data_dir
        self.http_factory = http_factory
        self.job: FetchJob | None = None
        self.years: list[int] = []
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def available_years(self) -> list[int]:
        if not self.years:
            async with self.http_factory() as http:
                self.years = await fetch_years(http)
        return self.years

    def start(self, years: list[int]) -> bool:
        if self.running:
            return False
        self.job = FetchJob(years=sorted(years))
        self.job.steps.append(f"向教育部網站要求產生 {'、'.join(map(str, self.job.years))} 學年度檔案")
        self._task = asyncio.create_task(self._run(self.job))
        return True

    async def _run(self, job: FetchJob) -> None:
        label = "、".join(map(str, job.years))
        try:
            async with self.http_factory() as http:
                filename, data = await download_years(http, job.years)
            job.steps.append(f"已下載 {len(data) / 1024:,.0f} KB")
            folder = self.data_dir / "moe"
            folder.mkdir(parents=True, exist_ok=True)
            job.file_path = folder / f"{label.replace('、', '-')}學年度_{filename}"
            job.file_path.write_bytes(data)
            job.steps.append("匯入系統")
            with self.session_factory() as session:
                imp = import_moe(session, job.file_path.name, data)
                job.row_count = session.scalar(select(func.count()).where(TeacherCountRow.teacher_import_id == imp.id))
                version = latest_version(session)
                if version:
                    auto_link(session, version.id, imp.id)
            job.status = "done"
        except MoeFetchError as e:
            job.status, job.error = "failed", str(e)
        except httpx.HTTPError as e:
            job.status, job.error = "failed", f"無法連線教育部網站（{type(e).__name__}）"
        except ValueError as e:
            job.status, job.error = "failed", f"下載的檔案無法匯入：{e}"
