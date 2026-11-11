import argparse
from types import SimpleNamespace

import httpx

import scripts.check_pubset_selfcite as check
from tests.elsevier_fixtures import ABSTRACT_URL, SCIVAL_URL, abstract_full, scival_handler

# 7 篇測試論文之間實際的引用（2026-11-10 驗證時找到）；"999…" 是組外的論文
REFS = {"85141566632": ["85120042660", "99900000001"], "85144826309": ["85120042660"],
        "85178007617": ["85144826309"], "85170429613": ["85093682401", "99900000002"]}


def args(**kw):
    return argparse.Namespace(**{"ids": None, "file": None, "web": None, "rate": 1000.0, **kw})


def install(respx_mock, monkeypatch):
    monkeypatch.setattr(check, "Settings", lambda: SimpleNamespace(elsevier_api_key="k", elsevier_insttoken=""))
    respx_mock.get(url__regex=SCIVAL_URL).mock(side_effect=scival_handler)
    respx_mock.get(url__regex=ABSTRACT_URL).mock(side_effect=lambda req: httpx.Response(
        200, json=abstract_full(REFS.get(req.url.path.rsplit("/", 1)[-1], []))))


async def test_sample_matches_scival_web(respx_mock, monkeypatch, capsys):
    install(respx_mock, monkeypatch)
    assert await check.run(args()) == 0
    out = capsys.readouterr().out
    assert "含自我引用 44，組內互引 4，排除後 40" in out and "→ 相同" in out


async def test_custom_ids_compare_with_given_web_numbers(respx_mock, monkeypatch, tmp_path, capsys):
    install(respx_mock, monkeypatch)
    f = tmp_path / "eids.txt"
    f.write_text("2-s2.0-85120042660\n2-s2.0-85141566632\n", encoding="utf-8")
    assert await check.run(args(file=str(f), web=[9, 7])) == 1   # 實際 9 → 8
    assert "排除後 8" in capsys.readouterr().out
    assert await check.run(args(ids=["85120042660"])) == 2        # 沒有網頁數字可比
