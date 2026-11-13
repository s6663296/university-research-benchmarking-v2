import json

from app.db.models import Run


def test_department_count_with_full_list_on_hover(client, web_done_run):
    """首頁與版本紀錄只列系所數量，完整清單放在 title，表格不會被長系所名稱撐寬。"""
    names = [f"系所{i}" for i in range(1, 7)]
    with client.app.state.session_factory() as session:
        run = session.get(Run, web_done_run)
        config = json.loads(run.scope_json)
        config["ncu_depts"] = names
        run.scope_json = json.dumps(config, ensure_ascii=False)
        session.commit()

    for path in ("/", "/runs"):
        text = client.get(path).text
        assert "6 個系所" in text and f'title="{"、".join(names)}"' in text and "展開系所" not in text
