"""Lightweight regression checks for the standalone Nexus chat shell."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "nexus_v2.html").read_text()
JS = (ROOT / "static" / "nexus-v2.js").read_text()


def test_chat_shell_has_all_controls():
    required = (
        "messages", "prompt", "send", "new", "menu", "connect-top",
        "settings-top", "settings", "token", "connect", "disconnect",
        "history", "history-search", "export-chat", "attachments",
        "file-picker", "camera-picker", "photo-picker", "tools-toggle",
        "voice-btn", "diagnose", "source-go", "finance-go", "availability",
    )
    for element in required:
        assert f'id="{element}"' in HTML, f"Missing required element: {element}"


def test_chat_response_is_not_raw_json_by_default():
    assert "data.synthesis||data.answer||data.error" in JS
    assert "window.NexusMarkdown.render" in JS
    assert "addResponseActions" in JS


def test_chat_uses_secure_existing_endpoints():
    for endpoint in ("/api/providers", "/api/jobs", "/api/files/extract", "/api/providers/check"):
        assert endpoint in JS
    assert "X-Studio-Token" in JS
    assert "trade_execution" not in JS


def test_no_legacy_mission_selector():
    assert 'id="mission"' not in HTML
    assert 'id="workflow-tag"' not in HTML
