"""真實 API 測試：需在校內網路或 VPN 下，設定 RUN_LIVE_API_TESTS=1 與 ELSEVIER_API_KEY 才會執行。"""

import os

import pytest

from app.config import Settings
from app.elsevier.client import ElsevierClient
from app.elsevier.scival import MetricsFetcher
from app.elsevier.scopus import build_query, search_docs

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_API_TESTS") != "1", reason="設定 RUN_LIVE_API_TESTS=1 才執行")


async def test_scopus_and_scival_agree():
    settings = Settings()
    client = ElsevierClient(settings.elsevier_api_key, settings.elsevier_insttoken)
    try:
        query = build_query("Department of Quantitative Finance National Tsing-Hua University", 2021, 2025)
        docs = await search_docs(client, query, use_cache=False)
        assert len(docs) > 0
        fetcher = MetricsFetcher(client, year_range="5yrsAndCurrentAndFuture", include_self="false",
                                 use_cache=False)
        metrics = await fetcher.fetch([d.scopus_id for d in docs])
        assert len(metrics) == len(docs)
        assert all(m.citations is not None for m in metrics.values())
        assert fetcher.data_source.get("sourceName") == "Scopus"
    finally:
        await client.aclose()
