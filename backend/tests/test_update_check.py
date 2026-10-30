"""Auto-update check route — best-effort, never breaks on network failure."""
import backend.main as m  # noqa: E402
import backend.app.routes.updates as up


def _fake_release(tag, url="https://github.com/amitjha2006/SMI_Managment_App/releases/tag/v1.0.1", notes="notes"):
    return {"tag_name": tag, "html_url": url, "body": notes, "published_at": "2026-01-01", "assets": []}


def test_update_available(monkeypatch):
    monkeypatch.setattr(up, "_read_version_file", lambda: "1.0.0")
    monkeypatch.setattr(up, "_fetch_latest_release", lambda: _fake_release("v1.0.1"))
    out = m.update_check()
    assert out["current_version"] == "1.0.0"
    assert out["latest_version"] == "1.0.1"
    assert out["update_available"] is True
    assert out["release_url"].endswith("/v1.0.1")


def test_no_update_when_current(monkeypatch):
    monkeypatch.setattr(up, "_read_version_file", lambda: "1.0.0")
    monkeypatch.setattr(up, "_fetch_latest_release", lambda: _fake_release("v1.0.0"))
    out = m.update_check()
    assert out["update_available"] is False


def test_no_update_when_offline(monkeypatch):
    monkeypatch.setattr(up, "_read_version_file", lambda: "1.0.0")
    monkeypatch.setattr(up, "_fetch_latest_release", lambda: None)
    out = m.update_check()
    assert out["update_available"] is False
    assert out["latest_version"] == "1.0.0"


def test_falls_back_when_no_version_file(monkeypatch):
    monkeypatch.setattr(up, "_read_version_file", lambda: None)
    monkeypatch.setattr(up, "_fetch_latest_release", lambda: _fake_release("v2.0.0"))
    out = m.update_check()
    assert out["current_version"] == "0.1.0"
    assert out["update_available"] is True
