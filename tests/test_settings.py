from __future__ import annotations

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings


def test_ui_settings_defaults_and_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    initial = s.load_ui_settings()
    assert initial["codebuddy_command"] == "codebuddy"
    assert initial["imap"]["port"] == 993

    # dsh 的路径以 backends 为权威：改 backends.dsh.command 后扁平字段 dsh_path 应镜像它。
    initial["backends"]["dsh"]["command"] = "/opt/dsh"
    initial["imap"]["host"] = "imap.example.com"
    s.save_ui_settings(initial)

    loaded = s.load_ui_settings()
    assert loaded["backends"]["dsh"]["command"] == "/opt/dsh"
    assert loaded["dsh_path"] == "/opt/dsh"  # 扁平字段从 backends 派生，保持一致
    assert loaded["imap"]["host"] == "imap.example.com"


def test_ui_settings_blank_password_keeps_existing(tmp_path):
    s = Settings(data_dir=tmp_path)
    data = s.load_ui_settings()
    data["imap"]["password"] = "secret"
    s.save_ui_settings(data)

    # The UI masks the secret and submits an empty value on unrelated edits.
    s.save_ui_settings({"imap": {"password": ""}})
    assert s.load_ui_settings()["imap"]["password"] == "secret"


def test_settings_api_roundtrip(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert r.json()["executor"] == "codebuddy"

        payload = {
            "executor": "codebuddy",
            "codebuddy_command": "npx -y @tencent-ai/codebuddy-code@latest",
            "dsh_path": "/opt/dsh",
            "default_model": "deep-model",
            "max_concurrent": 2,
            "auto_sync": False,
            "imap": {"host": "imap.example.com", "port": 993, "user": "u@e.com",
                     "password": "pw", "use_ssl": True},
        }
        r = client.put("/api/settings", json=payload)
        assert r.status_code == 200
        saved = r.json()
        assert saved["codebuddy_command"] == payload["codebuddy_command"]
        assert saved["auto_sync"] is False
        assert saved["imap"]["password"] == "pw"

        # Reload through the app to prove persistence, not just response echo.
        assert client.get("/api/settings").json() == saved
