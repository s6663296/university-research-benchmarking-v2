"""學校的 Scopus 機構代碼（AF-ID）：用英文校名查 Scopus Affiliation Search，查到後存起來重複使用。

只有校名互相包含的學校（台大 ⊂ 台科大）需要：查詢時用代碼排除他校論文，見 scopus.build_query。
"""

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import SchoolAffiliation
from app.elsevier.client import ElsevierClient

SEARCH_PATH = "/content/search/affiliation"
PAGE_SIZE = 50


@dataclass(frozen=True)
class Candidate:
    af_id: str
    name: str
    city: str
    doc_count: int


def name_key(name: str) -> str:
    """比對校名用：忽略大小寫、空白、標點與開頭的 The（Sun Yat-sen = Sun Yat-Sen = Sun Yat Sen）。"""
    key = re.sub(r"[^0-9a-z]", "", name.lower())
    return key[3:] if key.startswith("the") else key


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def search_candidates(client: ElsevierClient, school_en: str) -> list[Candidate]:
    clean = " ".join(re.sub(r'["()]', " ", school_en).split())
    data = await client.get_json(SEARCH_PATH, {"query": f'AFFIL("{clean}") AND AFFILCOUNTRY(Taiwan)',
                                               "count": PAGE_SIZE}, use_cache=False, store=False)
    out = []
    for e in data.get("search-results", {}).get("entry", []):
        af_id = str(e.get("dc:identifier", "")).replace("AFFILIATION_ID:", "")
        if af_id.isdigit():
            out.append(Candidate(af_id, e.get("affiliation-name") or "", e.get("city") or "",
                                 _int(e.get("document-count"))))
    return out


def pick(school_en: str, candidates: list[Candidate]) -> Candidate | None:
    """名稱與校名完全相同（忽略大小寫與標點）者中論文數最多的那筆（學校本部）；沒有完全相同的就不猜。"""
    same = [c for c in candidates if name_key(c.name) == name_key(school_en)]
    return max(same, key=lambda c: c.doc_count) if same else None


def get(session: Session, school_en: str) -> SchoolAffiliation | None:
    return session.get(SchoolAffiliation, name_key(school_en))


def af_ids(session: Session, schools: Iterable[str]) -> dict[str, str]:
    """{校名: AF-ID}，只含已確定代碼的學校。"""
    out = {}
    for s in schools:
        row = get(session, s)
        if row is not None and row.af_id:
            out[s] = row.af_id
    return out


def candidates_of(row: SchoolAffiliation | None) -> list[Candidate]:
    return [Candidate(**c) for c in json.loads(row.candidates_json or "[]")] if row else []


async def resolve(session: Session, client: ElsevierClient, schools: Iterable[str]) -> list[str]:
    """查出還沒有代碼的學校，回傳仍無法自動判定、需要人工選擇的校名。已有代碼（含人工指定）的不重查。

    全部查完才寫入：查詢期間不能持有寫入交易，否則 client 記錄額度時會遇到 database is locked。
    """
    todo = [s for s in dict.fromkeys(schools) if not ((row := get(session, s)) and row.af_id)]
    found = {s: await search_candidates(client, s) for s in todo}
    unresolved = []
    for school, cands in found.items():
        chosen = pick(school, cands)
        row = get(session, school)
        if row is None:
            row = SchoolAffiliation(school_key=name_key(school))
            session.add(row)
        row.school_en = school
        row.af_id = chosen.af_id if chosen else None
        row.af_name = chosen.name if chosen else None
        row.matched_by = "auto"
        row.candidates_json = json.dumps([asdict(c) for c in cands[:10]], ensure_ascii=False)
        row.updated_at = datetime.now()
        if chosen is None:
            unresolved.append(school)
    session.commit()
    return unresolved


def set_manual(session: Session, school_en: str, af_id: str) -> None:
    """人工指定代碼；名稱從候選清單帶入（不在清單中的代碼名稱留空）。"""
    af_id = af_id.strip()
    if not af_id.isdigit():
        raise ValueError("機構代碼必須是數字")
    row = get(session, school_en)
    if row is None:
        row = SchoolAffiliation(school_key=name_key(school_en), candidates_json="[]")
        session.add(row)
    match = next((c for c in candidates_of(row) if c.af_id == af_id), None)
    row.school_en = school_en
    row.af_id = af_id
    row.af_name = match.name if match else None
    row.matched_by = "manual"
    row.updated_at = datetime.now()
    session.commit()
