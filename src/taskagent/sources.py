"""Task sources: turn raw emails / manual input into WorkItems.

Rule-based only (no LLM in this phase):
  - title  = subject, with common [BUG]/【需求】 prefixes recognized
  - kind   = keyword classification (bug/缺陷 vs 需求/feature)
  - git    = extract git URLs from body
  - description = body minus signatures/quotes (best effort)

A real Agent Mail adapter only has to produce RawItem; the parser is shared.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .gitmap import extract_git_urls
from .models import ItemKind, SourceType, WorkItem

_BUG_WORDS = ("bug", "缺陷", "故障", "报错", "异常", "错误", "修复", "fix", "crash", "崩溃", "问题", "500")
_FEATURE_WORDS = ("需求", "feature", "功能", "新增", "支持", "增加", "实现", "优化")

_PREFIX_RE = re.compile(r"^\s*(\[|\【)(bug|需求|feature|缺陷|fix|task)(\]|\】)\s*", re.IGNORECASE)
_QUOTE_RE = re.compile(r"^\s*[>｜>]+", re.MULTILINE)
_SIG_SPLIT_RE = re.compile(r"\n\s*(--|此致|谢谢| regards|Best\s+Regards|发件人|From:)", re.IGNORECASE)


@dataclass
class RawItem:
    source: SourceType
    title: str
    body: str
    source_ref: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    git_url_hint: str = ""

    @classmethod
    def from_email(
        cls, *, subject: str, body: str, message_id: str = "", attachments: list[dict] | None = None
    ) -> RawItem:
        return cls(
            source=SourceType.EMAIL,
            title=subject,
            body=body,
            source_ref=message_id,
            attachments=attachments or [],
        )

    @classmethod
    def from_tapd(cls, item: Any) -> RawItem:
        """Convert a normalised TAPD item (tapd.TapdItem) to a RawItem.

        The TAPD item is the authoritative source, so its title/description win
        and ``source_ref`` is the stable ``tapd:<type>:<id>`` used for dedupe.
        """
        entity = getattr(item, "entity_type", "story")
        prefix = "[缺陷]" if entity == "bug" else ("[需求]" if entity == "story" else "[任务]")
        meta_lines = []
        if getattr(item, "owner", ""):
            meta_lines.append(f"处理人：{item.owner}")
        if getattr(item, "priority", ""):
            meta_lines.append(f"优先级：{item.priority}")
        if getattr(item, "external_url", ""):
            meta_lines.append(f"原始链接：{item.external_url}")
        body = getattr(item, "description", "") or ""
        if meta_lines:
            body = "\n".join(meta_lines) + "\n\n" + body
        return cls(
            source=SourceType.TAPD,
            title=f"{prefix} {getattr(item, 'title', '')}".strip(),
            body=body,
            source_ref=item.ref,
            git_url_hint=getattr(item, "git_url_hint", ""),
        )


def classify_kind(title: str, body: str) -> ItemKind:
    # Explicit [BUG]/【需求】 prefix wins — it is the strongest signal.
    prefix = _PREFIX_RE.match(title)
    if prefix:
        marker = prefix.group(2).lower()
        if marker in {"bug", "缺陷", "fix"}:
            return ItemKind.BUG
        if marker in {"需求", "feature"}:
            return ItemKind.FEATURE
    text = f"{title} {body[:500]}".lower()
    bug_hits = sum(1 for word in _BUG_WORDS if word in text)
    feature_hits = sum(1 for word in _FEATURE_WORDS if word in text)
    if bug_hits > feature_hits:
        return ItemKind.BUG
    if feature_hits > 0:
        return ItemKind.FEATURE
    return ItemKind.TASK


def clean_title(subject: str) -> str:
    return _PREFIX_RE.sub("", subject).strip() or subject.strip()


def clean_body(body: str) -> str:
    text = _QUOTE_RE.sub("", body)
    match = _SIG_SPLIT_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def parse_raw(raw: RawItem, *, exec_mode: str = "dry-run") -> WorkItem:
    """Convert a RawItem into a WorkItem, extracting git URL from body/hint."""
    title = clean_title(raw.title)
    description = clean_body(raw.body)
    if raw.attachments:
        names = ", ".join(str(item.get("filename", "")) for item in raw.attachments)
        description += f"\n\n附件：{names}"
    urls = extract_git_urls(raw.title + " " + description + " " + raw.git_url_hint)
    git_url = raw.git_url_hint.strip() or (urls[0] if urls else "")
    dedupe = raw.source_ref or f"{raw.source.value}:{raw.title.strip().lower()}"
    return WorkItem.create(
        title=title,
        description=description,
        kind=classify_kind(raw.title, description).value,
        source=raw.source.value,
        source_ref=raw.source_ref,
        git_url=git_url,
        dedupe_key=dedupe,
        exec_mode=exec_mode,
    )


class Source(ABC):
    """A source yields RawItems; parsing/mapping is handled downstream."""

    name: str

    @abstractmethod
    def fetch(self) -> list[RawItem]: ...


class ManualSource(Source):
    """Explicitly supplied items (CLI/UI)."""

    name = "manual"

    def __init__(self, items: list[RawItem] | None = None) -> None:
        self._items = items or []

    def fetch(self) -> list[RawItem]:
        return list(self._items)


class EmailSource(Source):
    """Agent Mail adapter.

    Agent Mail is not bound yet in this environment, so fetch() is wired to a
    caller-provided list of messages (already fetched via the mail tool). When
    the mailbox is enabled, the caller passes real ListMessages/SearchMessages
    results here. No mail logic is hard-coded, so the seam stays clean.
    """

    name = "email"

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages = messages or []

    @staticmethod
    def from_mailtool(messages: list[dict[str, Any]]) -> list[RawItem]:
        raws: list[RawItem] = []
        for message in messages:
            raws.append(
                RawItem.from_email(
                    subject=message.get("subject", ""),
                    body=message.get("body") or message.get("snippet", ""),
                    message_id=message.get("id") or message.get("message_id", ""),
                    attachments=message.get("attachments"),
                )
            )
        return raws

    def fetch(self) -> list[RawItem]:
        return self.from_mailtool(self._messages)


class TapdSource(Source):
    """Authoritative source adapter: TAPD stories/bugs -> RawItem.

    Wraps :class:`~taskagent.tapd.TapdClient`. The client is synchronous
    (urllib), so callers should run ``fetch()`` in a worker thread via
    ``asyncio.to_thread`` to avoid blocking the event loop.
    """

    name = "tapd"

    def __init__(self, client: Any) -> None:
        self.client = client

    def fetch(self) -> list[RawItem]:
        result = self.client.fetch_all()
        return [RawItem.from_tapd(item) for item in result.items]

    def fetch_one(self, entity_type: str, object_id: str) -> RawItem:
        return RawItem.from_tapd(self.client.fetch_one(entity_type, object_id))
