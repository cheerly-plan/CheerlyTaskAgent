from __future__ import annotations

from email.message import EmailMessage

from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings
from taskagent.mailpoll import MailPoller, _body_from_message, _decode


def test_mail_poll_settings_defaults_and_roundtrip(tmp_path):
    s = Settings(data_dir=tmp_path)
    initial = s.load_ui_settings()
    assert initial["mail_poll"]["enabled"] is False
    assert initial["mail_poll"]["interval_minutes"] == 5
    assert initial["mail_poll"]["exec_mode"] == "dry-run"

    s.save_ui_settings({"mail_poll": {"enabled": True, "interval_minutes": 2, "exec_mode": "apply"}})
    loaded = s.load_ui_settings()["mail_poll"]
    assert loaded["enabled"] is True
    assert loaded["interval_minutes"] == 2
    assert loaded["exec_mode"] == "apply"

    # partial update keeps the other fields
    s.save_ui_settings({"mail_poll": {"enabled": False}})
    partial = s.load_ui_settings()["mail_poll"]
    assert partial["enabled"] is False
    assert partial["interval_minutes"] == 2


def test_mail_poll_normalises_bad_values(tmp_path):
    s = Settings(data_dir=tmp_path)
    s.save_ui_settings({"mail_poll": {"interval_minutes": 0, "exec_mode": "bogus"}})
    poll = s.load_ui_settings()["mail_poll"]
    assert poll["interval_minutes"] == 5
    assert poll["exec_mode"] == "dry-run"


def test_poll_without_credentials_returns_error():
    result = MailPoller({}).poll()
    assert result.error is not None
    assert "凭证" in result.error
    assert result.messages == []


def test_decode_and_body_helpers():
    assert _decode(None) == ""
    msg = EmailMessage()
    msg["Subject"] = "plain subject"
    msg.set_content("hello body\nsecond line")
    assert "hello body" in _body_from_message(msg)

    # multipart with an HTML part falls back gracefully
    msg2 = EmailMessage()
    msg2.set_content("text fallback")
    msg2.add_alternative("<p>html</p>", subtype="html")
    assert _body_from_message(msg2)


def test_mail_pull_endpoint_reports_missing_credentials(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        r = client.post("/api/mail/pull")
        # No IMAP credentials configured -> 400 with a clear message.
        assert r.status_code == 400
        assert "凭证" in r.json()["detail"]
