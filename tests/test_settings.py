from __future__ import annotations

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings


def test_ui_settings_defaults_and_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    initial = s.load_ui_settings()
    assert initial["codebuddy_command"] == "codebuddy"

    # dsh 的路径以 backends 为权威：改 backends.dsh.command 后扁平字段 dsh_path 应镜像它。
    initial["backends"]["dsh"]["command"] = "/opt/dsh"
    s.save_ui_settings(initial)

    loaded = s.load_ui_settings()
    assert loaded["backends"]["dsh"]["command"] == "/opt/dsh"
    assert loaded["dsh_path"] == "/opt/dsh"  # 扁平字段从 backends 派生，保持一致


def test_ui_settings_blank_password_keeps_existing(tmp_path):
    s = Settings(data_dir=tmp_path)
    data = s.load_ui_settings()
    data["tapd"]["api_password"] = "secret"
    s.save_ui_settings(data)

    # The UI masks the secret and submits an empty value on unrelated edits.
    s.save_ui_settings({"tapd": {"api_password": ""}})
    assert s.load_ui_settings()["tapd"]["api_password"] == "secret"


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
        }
        r = client.put("/api/settings", json=payload)
        assert r.status_code == 200
        saved = r.json()
        assert saved["codebuddy_command"] == payload["codebuddy_command"]
        assert saved["auto_sync"] is False

        # Reload through the app to prove persistence, not just response echo.
        assert client.get("/api/settings").json() == saved


def test_saving_settings_applies_without_restart(tmp_path):
    """PUT /api/settings must refresh executors and concurrency in place.

    Previously both were snapshotted at startup, so the UI told the user to
    restart the service. Saving should now take effect for the next run.
    """
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        before = client.get("/api/config").json()
        assert before["max_concurrent"] == 1

        payload = client.get("/api/settings").json()
        payload["max_concurrent"] = 3
        payload["backends"]["dsh"]["command"] = "/opt/custom-dsh"
        payload["backends"]["dsh"]["enabled"] = True
        payload["executor"] = "dsh"
        saved = client.put("/api/settings", json=payload)
        assert saved.status_code == 200

        after = client.get("/api/config").json()
        # concurrency and default backend are live
        assert after["max_concurrent"] == 3
        assert after["executor"] == "dsh"
        dsh = next(b for b in after["backends"] if b["id"] == "dsh")
        # the rebuilt executor reads the new command (no restart)
        assert dsh["command"].endswith("custom-dsh")
        # scheduler's gate was resized too
        assert app.state.ctx.scheduler.settings.max_concurrent == 3


def test_backend_probe_reports_capability_on_demand(tmp_path):
    """The probe endpoint verifies capability without being on the config path.

    /api/config must not spawn CLI processes; the UI calls this once, the first
    time the user opens a model picker.
    """
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        # config is optimistic (no probe yet)
        config = client.get("/api/config").json()
        dsh = next(b for b in config["backends"] if b["id"] == "dsh")
        assert dsh["model_selection"] is True

        # probing a backend whose CLI cannot be run degrades to False, not a 500
        probed = client.post("/api/backends/dsh/probe")
        assert probed.status_code == 200
        body = probed.json()
        assert body["id"] == "dsh"
        assert body["model_selection"] is False
        assert isinstance(body["models"], list)

        missing = client.post("/api/backends/nope/probe")
        assert missing.status_code == 404


def test_discover_command_keeps_bare_name_when_on_path(monkeypatch):
    """PATH resolution wins: the bare name stays portable across machines."""
    from taskagent import config

    monkeypatch.setattr(config.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert config.discover_command("dsh") == "dsh"


def test_discover_command_falls_back_to_bundle(monkeypatch, tmp_path):
    """Desktop-app CLIs ship outside PATH; resolve the bundle launcher."""
    from taskagent import config

    fake = tmp_path / "dsh"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setitem(config.KNOWN_CLI_PATHS, "dsh", (str(fake),))
    assert config.discover_command("dsh") == str(fake)


def test_discover_command_trusts_explicit_path(monkeypatch):
    from taskagent import config

    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    assert config.discover_command("/opt/custom/dsh") == "/opt/custom/dsh"


def test_load_ui_settings_resolves_bundled_cli(monkeypatch, tmp_path):
    """A fresh install shows a usable path instead of a bare name."""
    from taskagent import config

    fake = tmp_path / "dsh"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setitem(config.KNOWN_CLI_PATHS, "dsh", (str(fake),))

    s = Settings(data_dir=tmp_path / "data")
    assert s.load_ui_settings()["backends"]["dsh"]["command"] == str(fake)
