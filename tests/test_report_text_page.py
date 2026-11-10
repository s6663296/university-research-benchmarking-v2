from app.db.models import Run
from app.reports.report_text import BY_KEY, FALLBACK_SAMPLES, VARIABLES, page_variables, sample_values
from tests.run_helpers import make_run


def test_each_page_only_offers_variables_it_can_use():
    assert "經費年數" in page_variables(BY_KEY["funding"]) and "經費年數" not in page_variables(BY_KEY["pubs"])
    assert "名次門檻" in page_variables(BY_KEY["summary"]) and "名次門檻" not in page_variables(BY_KEY["fwci"])
    assert "自我引用方式" in page_variables(BY_KEY["avg_citations"])
    assert {"期間", "系所", "資料日期"} <= set(page_variables(BY_KEY["fwci"]))


def test_samples_fall_back_without_finished_run(session):
    assert sample_values(session) == FALLBACK_SAMPLES


def test_samples_come_from_latest_finished_run(session):
    run = make_run(session)
    session.get(Run, run.id).status = "done"
    session.commit()
    samples = sample_values(session)
    assert samples["期間"] == f"{run.period_from}-{run.period_to}"
    assert set(samples) == set(VARIABLES) and all(samples.values())


def test_page_has_item_list_instead_of_variable_table(client):
    text = client.get("/report-text").text
    assert "可用的變數" not in text
    assert "data-report-nav" in text and 'href="#fwci"' in text and 'id="report-variables"' in text


def test_only_modified_items_are_marked(client):
    client.post("/report-text", data={"pubs.name": "新名稱"})
    text = client.get("/report-text").text
    nav = text.split("data-report-nav")[1].split("</nav>")[0]
    pubs, top1 = nav.split('href="#pubs"')[1].split("</a>")[0], nav.split('href="#top1"')[1].split("</a>")[0]
    assert "已修改" in pubs and "已修改" not in top1


def test_save_returns_to_current_item(client):
    r = client.post("/report-text", data={"active_key": "fwci"}, follow_redirects=False)
    assert r.headers["location"] == "/report-text?saved=1#fwci"


def test_save_ignores_unknown_current_item(client):
    r = client.post("/report-text", data={"active_key": "nope"}, follow_redirects=False)
    assert r.headers["location"] == "/report-text?saved=1"
