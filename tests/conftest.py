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
    monkeypatch.setattr(bg, "_windows_dir", lambda: tmp_path / "windows")       # and its windows, for other processes


@pytest.fixture(autouse=True)
def a_text_reader(monkeypatch):
    """Plans offer click_text, wait_for and labelled clicks only where a text reader is installed (plan.can_read:
    macOS Vision or rapidocr). Tests fake the reader (plan.read_lines), so they get that plan everywhere, Linux CI
    included; a test about a computer with no reader sets can_read to False itself."""
    try:
        from legato import plan as plans
    except ImportError:
        return
    monkeypatch.setattr(plans, "can_read", lambda: True)
