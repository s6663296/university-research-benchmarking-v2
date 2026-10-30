"""真實 API 測試：需在校內網路或 VPN 下，設定 RUN_LIVE_API_TESTS=1 與 ELSEVIER_API_KEY 才會執行。"""

import os

import pytest

from app.config import Settings
from app.elsevier.client import ElsevierClient
from app.elsevier.scival import fetch_metrics
from app.elsevier.scopus import build_query, search_docs

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_API_TESTS") != "1", reason="設定 RUN_LIVE_API_TESTS=1 才執行")


async def test_scopus_and_scival_agree():
    settings = Settings()
    client = ElsevierClient(settings.elsevier_api_key, settings.elsevier_insttoken)
    try:
        query = build_query("Department of Quantitative Finance National Tsing-Hua University", 2021, 2025)
        docs = await search_docs(client, query, use_cache=False)
        assert len(docs) > 0
        metrics, source = await fetch_metrics(client, [d.scopus_id for d in docs], year_range="5yrs",
                                              batch_size=25, use_cache=False)
        assert len(metrics) == len(docs)
        assert source.get("sourceName") == "Scopus"
    finally:
        await client.aclose()
