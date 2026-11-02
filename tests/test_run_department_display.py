import json
from html.parser import HTMLParser

from app.db.models import Run


class DepartmentDetails(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details" and "department-list" in attrs.get("class", "").split():
            self.details.append(attrs)


def test_long_department_lists_are_collapsed_on_home_and_history(client, web_done_run):
    names = [f"系所{i}" for i in range(1, 7)]
    with client.app.state.session_factory() as session:
        run = session.get(Run, web_done_run)
        config = json.loads(run.scope_json)
        config["ncu_depts"] = names
        run.scope_json = json.dumps(config, ensure_ascii=False)
        session.commit()

    for path in ("/", "/runs"):
        text = client.get(path).text
        parser = DepartmentDetails()
        parser.feed(text)
        assert len(parser.details) == 1 and "open" not in parser.details[0]
        assert all(name in text for name in names)
        assert "展開系所" in text
        assert text.index(names[-1]) < text.index("收合系所")


def test_short_department_lists_need_no_disclosure(client, web_done_run):
    for path in ("/", "/runs"):
        text = client.get(path).text
        assert "資訊管理學系" in text
        assert 'class="department-list"' not in text
