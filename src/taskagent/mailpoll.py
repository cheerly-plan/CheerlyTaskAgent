"""IMAP mail poller: fetch inbox messages and hand them to EmailSource.

Uses only the standard library (imaplib/email). The poller is deliberately
dumb: it fetches messages and returns them as the plain dict shape that
``EmailSource.from_mailtool`` already understands. Parsing, de-dup by
message-id, mapping and scheduling all happen downstream via the existing
``scheduler.import_raw`` path.

Failures are reported, never raised into the server loop: a bad password or
an unreachable mailbox must not take down polling or the app.
"""

from __future__ import annotations

import email
import imaplib
import logging
from dataclasses import dataclass
from email.header import decode_header, make_header
from typing import Any

logger = logging.getLogger("taskagent")


@dataclass
class PollResult:
    messages: list[dict[str, Any]]
    error: str | None = None


def _decode(value: str | None) -> str:
    """Decode an RFC2047 encoded header (subject/from) into unicode text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 - malformed header must not drop the message
        return value


def _body_from_message(msg: email.message.Message) -> str:
    """Extract a readable plain-text body from a fetched email message."""
    if msg.is_multipart():
        # Prefer text/plain, fall back to the first text/* part.
        fallback = ""
        for part in msg.walk():
            if part.get_content_maintype() != "text":
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if part.get_content_subtype() == "plain":
                return text
            fallback = fallback or text
        return fallback
    payload = msg.get_payload(decode=True)
    if payload is None:
        return str(msg.get_payload() or "")
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _snippet(body: str, limit: int = 4000) -> str:
    text = body.strip()
    return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


class MailPoller:
    """Fetch messages from an IMAP inbox using UI-managed credentials."""

    def __init__(self, imap_cfg: dict[str, Any], *, mailbox: str = "INBOX", limit: int = 50) -> None:
        self.cfg = imap_cfg or {}
        self.mailbox = mailbox
        self.limit = limit

    def poll(self) -> PollResult:
        host = str(self.cfg.get("host", "")).strip()
        user = str(self.cfg.get("user", "")).strip()
        password = str(self.cfg.get("password", "")).strip()
        if not host or not user or not password:
            return PollResult([], error="IMAP 凭证不完整（主机 / 账号 / 密码）")

        try:
            port = int(self.cfg.get("port", 993) or 993)
            use_ssl = self.cfg.get("use_ssl", True) is not False
            if use_ssl:
                client = imaplib.IMAP4_SSL(host, port)
            else:
                client = imaplib.IMAP4(host, port)
            try:
                client.login(user, password)
                status, _ = client.select(self.mailbox, readonly=True)
                if status != "OK":
                    return PollResult([], error=f"无法打开邮箱文件夹 {self.mailbox}")

                # UNSEEN keeps this incremental; de-dup by message-id downstream
                # still guards against reprocessing the same message.
                status, data = client.search(None, "UNSEEN")
                if status != "OK":
                    return PollResult([], error="搜索未读邮件失败")
                ids = (data[0] or b"").split()
                ids = ids[-self.limit :]  # newest N

                messages: list[dict[str, Any]] = []
                for raw_id in ids:
                    status, msg_data = client.fetch(raw_id, "(RFC822)")
                    if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    body = _body_from_message(msg)
                    messages.append(
                        {
                            "id": (str(msg.get("Message-ID") or "").strip() or raw_id.decode(errors="replace")),
                            "subject": _decode(msg.get("Subject")),
                            "body": _snippet(body),
                            "from": _decode(msg.get("From")),
                        }
                    )
                return PollResult(messages)
            finally:
                try:
                    client.close()
                except Exception:
                    logger.debug("关闭 IMAP 邮箱失败", exc_info=True)
                try:
                    client.logout()
                except Exception:
                    logger.debug("IMAP 登出失败", exc_info=True)
        except imaplib.IMAP4.error as error:
            return PollResult([], error=f"IMAP 认证/协议错误：{error}")
        except Exception as error:  # noqa: BLE001 - network errors, timeouts, etc.
            return PollResult([], error=f"拉取邮件失败：{error}")
