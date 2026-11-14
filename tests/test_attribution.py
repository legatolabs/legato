from legato.rl.policy import attribution_headers


def test_openrouter_gets_attribution(monkeypatch):
    monkeypatch.delenv("LEGATO_NO_ATTRIBUTION", raising=False)
    h = attribution_headers("https://openrouter.ai/api/v1")
    assert h == {"HTTP-Referer": "https://legato.computer", "X-Title": "Legato"}


def test_other_servers_and_opt_out(monkeypatch):
    monkeypatch.delenv("LEGATO_NO_ATTRIBUTION", raising=False)
    assert attribution_headers("http://localhost:8000/v1") == {}
    monkeypatch.setenv("LEGATO_NO_ATTRIBUTION", "1")
    assert attribution_headers("https://openrouter.ai/api/v1") == {}
