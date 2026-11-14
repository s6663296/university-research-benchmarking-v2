import argparse
from types import SimpleNamespace

import scripts.check_pubset_selfcite as check
from app.elsevier.scival import citation_count
from app.elsevier.scopus import build_query
from tests.elsevier_fixtures import ids67, load
from tests.run_helpers import FakeElsevier, fake_client

# 7 篇測試論文之間實際的引用（2026-11-10 驗證時找到）；"999…" 是組外的論文
REFS = {"85141566632": ["85120042660", "99900000001"], "85144826309": ["85120042660"],
        "85178007617": ["85144826309"], "85170429613": ["85093682401", "99900000002"]}


def args(**kw):
    return argparse.Namespace(**{"ids": None, "file": None, "web": None, "dept": None, "years": None,
                                 "save": None, "show": 30, "scopus_only": False, **kw})


def patch_client(monkeypatch):
    monkeypatch.setattr(check, "Settings", lambda: SimpleNamespace(elsevier_api_key="k", elsevier_insttoken=""))
    monkeypatch.setattr(check, "open_client", lambda settings: fake_client())
    monkeypatch.setattr(check, "mapping_schools", lambda settings: [])


def install(respx_mock, monkeypatch, **kw):
    patch_client(monkeypatch)
    fake = FakeElsevier(refs=REFS, **kw)
    fake.install(respx_mock)
    return fake


async def test_sample_matches_scival_web(respx_mock, monkeypatch, capsys):
    install(respx_mock, monkeypatch)
    assert await check.run(args()) == 0
    out = capsys.readouterr().out
    assert "含自我引用 44，組內互引 4，排除後 40" in out and "→ 相同" in out
    assert "2021 14→13、2022 11→8、2023 14→14、2024 5→5" in out   # 與網頁截圖逐年相同


async def test_custom_ids_compare_with_given_web_numbers(respx_mock, monkeypatch, tmp_path, capsys):
    install(respx_mock, monkeypatch)
    f = tmp_path / "eids.txt"
    f.write_text("2-s2.0-85120042660\n2-s2.0-85141566632\n", encoding="utf-8")
    assert await check.run(args(file=str(f), web=[9, 7])) == 1   # 實際 9 → 8
    out = capsys.readouterr().out
    assert "排除後 8" in out and "網頁差額 2，系統的組內互引 1" in out
    assert await check.run(args(ids=["85120042660"])) == 2        # 沒有網頁數字可比


async def test_dept_saves_eids_and_uses_same_query_as_system(respx_mock, monkeypatch, tmp_path, capsys):
    patch_client(monkeypatch)
    ids = ids67()[0:20]                                            # 模擬伺服器中「中央資管」的論文
    FakeElsevier(refs={ids[1]: [ids[0]], ids[2]: [ids[0], ids[1]]}).install(respx_mock)
    base = {str(r["publication"]["id"]): citation_count(r) for r in load("scival_cites_qf_67.json")["results"]}
    total = sum(base[i] for i in ids)
    save = tmp_path / "eids.txt"
    code = await check.run(args(dept="Department of Information Management, National Central University",
                                years=[2021, 2025], save=str(save), web=[total, total - 3]))
    assert code == 0
    assert save.read_text(encoding="utf-8").split() == [f"2-s2.0-{i}" for i in ids]
    out = capsys.readouterr().out
    assert build_query("Department of Information Management, National Central University", 2021, 2025) in out
    assert f"20 篇（SciVal 網頁的 Scholarly Output 應相同），含自我引用 {total}，組內互引 3" in out


async def test_warns_when_paper_count_entered_as_citations(respx_mock, monkeypatch, capsys):
    install(respx_mock, monkeypatch)
    assert await check.run(args(web=[7, 40])) == 1
    assert "可能輸入成 Scholarly Output" in capsys.readouterr().out


async def test_sample_scopus_only_needs_no_abstract_retrieval(respx_mock, monkeypatch, capsys):
    fake = install(respx_mock, monkeypatch)
    assert await check.run(args(scopus_only=True)) == 0
    assert fake.abstract_calls == []
    assert "含自我引用 44，組內互引 4，排除後 40" in capsys.readouterr().out


async def test_sample_fetches_only_papers_citing_the_set(respx_mock, monkeypatch, capsys):
    fake = install(respx_mock, monkeypatch)
    assert await check.run(args()) == 0
    assert sorted(fake.abstract_calls) == sorted(REFS)   # 7 篇中只有 4 篇引用了組內論文
