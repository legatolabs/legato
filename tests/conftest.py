"""Every test gets its own empty Legato config and no model keys, so the machine's own `legato setup` choice and
keys never leak in."""
import pytest


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGATO_CONFIG", str(tmp_path / "legato-config.json"))
    for name in ("LEGATO_MODEL", "LEGATO_BASE_URL", "LEGATO_API_KEY", "LEGATO_DESKTOP", "LEGATO_APP",
                 "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "legato-config.json"


@pytest.fixture(autouse=True)
def no_shared_stall_note(tmp_path, monkeypatch):
    """Background mode notes a window-capture stall in ~/.cache for the next process: tests keep theirs private."""
    try:
        import legato.background as bg
    except ImportError:
        return
    monkeypatch.setattr(bg, "_stall_file", lambda: tmp_path / "capture-stalled")
