from app.config import save_env


def test_home_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<h1>首頁</h1>" in r.text
    assert "htmx.min.js" in r.text and "cdn" not in r.text.lower()
    assert 'aria-label="返回首頁"' not in r.text


def test_subpages_offer_back_navigation(client):
    for path in ("/mapping", "/teachers", "/runs/new", "/runs", "/settings"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'href="/" aria-label="返回首頁"' in response.text


def test_save_env_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FOO=1\nELSEVIER_API_KEY=old\n", encoding="utf-8")
    save_env(env, "new", "tok")
    text = env.read_text(encoding="utf-8")
    assert "FOO=1" in text and "ELSEVIER_API_KEY=new" in text and "ELSEVIER_INSTTOKEN=tok" in text
    assert "old" not in text
