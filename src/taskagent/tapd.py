"""TAPD Open API client: authoritative source for stories/bugs.

Design (see docs/architecture.md):
  - TAPD is the source of truth. Webhooks / emails only *trigger* a sync;
    the full details are always pulled from this API before creating a task.
  - Basic Auth: api_user:api_password (TAPD app id / app secret).
  - Public cloud: https://api.tapd.cn  ·  Internal (Tencent): https://apiv2.tapd.woa.com
  - Pull: GET /stories, GET /bugs  (workspace_id, limit<=200, page, modified)
  - Write-back: POST /stories or /bugs with id + status + current_user.

No credentials are stored in the repo — they come from UI settings
(data/settings.json) passed in by the caller. Missing credentials produce a
clear error instead of crashing the service.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.tapd.cn"
INTERNAL_BASE_URL = "https://apiv2.tapd.woa.com"

# Entity type -> (plural API path, singular key in response).
_ENTITIES = {
    "story": ("stories", "Story"),
    "bug": ("bugs", "Bug"),
    "task": ("tasks", "Task"),
}


@dataclass
class TapdConfig:
    base_url: str = DEFAULT_BASE_URL
    workspace_id: str = ""
    api_user: str = ""
    api_password: str = ""
    webhook_secret: str = ""
    # Only these entity types are pulled. Empty means story+bug.
    entity_types: list[str] = field(default_factory=lambda: ["story", "bug"])
    # TAPD status codes to pull; empty means all non-closed. Examples:
    # ["planning", "in_progress"]. Keep empty by default to avoid surprises.
    pull_statuses: list[str] = field(default_factory=list)
    # Status code written back on task completion / failure.
    status_on_success: str = "resolved"
    status_on_failure: str = ""
    # Author name used for write-back (current_user field).
    current_user: str = "taskagent"

    def __post_init__(self) -> None:
        # 凭证从网页/终端复制时经常带上首尾空白、制表符或换行；Basic Auth 会
        # 原样 base64 编码，导致 TAPD 返回 401。统一在这里清理，覆盖所有构造入口。
        self.base_url = (self.base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        for attr in (
            "workspace_id", "api_user", "api_password", "webhook_secret",
            "status_on_success", "status_on_failure", "current_user",
        ):
            setattr(self, attr, (getattr(self, attr) or "").strip())

    @property
    def workspace_ids(self) -> list[str]:
        """多选项目：workspace_id 以逗号分隔；返回去重后的非空 ID 列表。"""
        parts = str(self.workspace_id or "").replace("，", ",").split(",")
        return list(dict.fromkeys(part.strip() for part in parts if part.strip()))

    @property
    def primary_workspace_id(self) -> str:
        """回退用的主项目 ID（多选时取第一个）。"""
        ids = self.workspace_ids
        return ids[0] if ids else ""

    def is_configured(self) -> bool:
        return bool(self.workspace_ids and self.api_user and self.api_password)

    def missing(self) -> list[str]:
        missing = []
        if not self.workspace_ids:
            missing.append("workspace_id")
        if not self.api_user:
            missing.append("api_user")
        if not self.api_password:
            missing.append("api_password")
        return missing


@dataclass
class TapdItem:
    """A normalised story/bug/task from TAPD."""

    entity_type: str
    id: str
    workspace_id: str
    title: str
    description: str
    status: str
    owner: str
    priority: str
    iteration_id: str
    created: str
    modified: str
    git_url_hint: str = ""
    parent_id: str = ""        # 父需求 id；顶层为 "0" 或空
    children_ids: list[str] = field(default_factory=list)  # 子需求 id 列表（TAPD children_id 格式 |id1|id2|）
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """Stable source_ref used for dedupe: tapd:story:12345."""
        return f"tapd:{self.entity_type}:{self.id}"

    @property
    def parent_ref(self) -> str:
        """source_ref of the parent; empty for top-level items."""
        pid = str(self.parent_id or "").strip()
        if not pid or pid == "0":
            return ""
        # 父子需求通常同类型（story→story）；task 的父固定是 story。
        parent_type = "story" if self.entity_type == "task" else self.entity_type
        return f"tapd:{parent_type}:{pid}"

    @property
    def external_url(self) -> str:
        ws = self.workspace_id
        kind = "story" if self.entity_type == "story" else ("bugtrace" if self.entity_type == "bug" else "task")
        return f"https://www.tapd.cn/{ws}/prong/{kind}/view/{self.id}"


@dataclass
class TapdResult:
    items: list[TapdItem] = field(default_factory=list)
    error: str = ""


class TapdError(RuntimeError):
    pass


class TapdClient:
    """Thin, synchronous TAPD API client (callers run it in a thread)."""

    def __init__(self, config: TapdConfig, *, timeout: float = 15.0) -> None:
        self.config = config
        self.timeout = timeout

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        base = self.config.base_url.rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            url += "?" + urllib.parse.urlencode(clean)
        return url

    def _request(self, method: str, path: str, *, params=None, data=None) -> dict[str, Any]:
        if not self.config.is_configured():
            raise TapdError("TAPD 未配置，缺少：" + ", ".join(self.config.missing()))
        url = self._url(path, params)
        headers = {
            "Authorization": "Basic "
            + base64.b64encode(
                f"{self.config.api_user}:{self.config.api_password}".encode()
            ).decode("ascii"),
            "Accept": "application/json",
        }
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise TapdError(f"TAPD HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TapdError(f"TAPD 网络错误：{exc}") from exc
        except ValueError as exc:
            raise TapdError(f"TAPD 返回非 JSON：{exc}") from exc
        if isinstance(payload, dict) and payload.get("status") not in (1, "1", None):
            raise TapdError(f"TAPD 返回错误：{payload.get('info', payload)}")
        return payload

    # ── pull ──────────────────────────────────────────────────────────────────
    def fetch(
        self,
        entity_type: str,
        *,
        modified_since: str = "",
        limit: int = 200,
        max_pages: int = 5,
    ) -> list[TapdItem]:
        if entity_type not in _ENTITIES:
            raise TapdError(f"不支持的实体类型：{entity_type}")
        if not self.config.workspace_ids:
            raise TapdError("TAPD 未配置，缺少：" + ", ".join(self.config.missing()))
        plural, singular = _ENTITIES[entity_type]
        items: list[TapdItem] = []
        seen: set[str] = set()
        # 多选项目：逐个工作区拉取，不能把 "1,2" 整体当成一个 workspace_id 传给 TAPD。
        for workspace_id in self.config.workspace_ids:
            for page in range(1, max_pages + 1):
                params: dict[str, Any] = {
                    "workspace_id": workspace_id,
                    "limit": min(200, max(1, limit)),
                    "page": page,
                    "order": "modified desc",
                }
                if self.config.pull_statuses:
                    params["status"] = ",".join(self.config.pull_statuses)
                if modified_since:
                    params["modified"] = f">{modified_since}"
                payload = self._request("GET", plural, params=params)
                rows = payload.get("data") or []
                if not rows:
                    break
                for row in rows:
                    obj = row.get(singular) if isinstance(row, dict) else None
                    if isinstance(obj, dict):
                        item = self._normalise(entity_type, obj)
                        if not item.workspace_id:
                            item.workspace_id = workspace_id
                        if item.ref not in seen:
                            seen.add(item.ref)
                            items.append(item)
                if len(rows) < min(200, max(1, limit)):
                    break
        return items

    def fetch_all(
        self, *, modified_since: str = "", limit: int = 200, max_pages: int = 5
    ) -> TapdResult:
        result = TapdResult()
        for entity_type in self.config.entity_types:
            try:
                result.items.extend(
                    self.fetch(entity_type, modified_since=modified_since, limit=limit, max_pages=max_pages)
                )
            except TapdError as exc:
                result.error = str(exc)
                logger.warning("拉取 TAPD %s 失败：%s", entity_type, exc)
        return result

    def fetch_one(self, entity_type: str, object_id: str) -> TapdItem:
        """Fetch a single item by id — used after a webhook notification."""
        if entity_type not in _ENTITIES:
            raise TapdError(f"不支持的实体类型：{entity_type}")
        plural, singular = _ENTITIES[entity_type]
        # Webhook 只带对象 ID，不知道属于哪个项目：多选时逐个工作区试。
        for workspace_id in self.config.workspace_ids:
            payload = self._request(
                "GET", plural,
                params={"workspace_id": workspace_id, "id": object_id},
            )
            rows = payload.get("data") or []
            if rows and singular in rows[0]:
                item = self._normalise(entity_type, rows[0][singular])
                if not item.workspace_id:
                    item.workspace_id = workspace_id
                return item
        raise TapdError(f"TAPD 找不到 {entity_type} {object_id}")

    def list_projects(self, company_id: str) -> list[dict[str, str]]:
        """列出企业的 TAPD 项目，供 UI 选择 workspace_id。

        走 TAPD 的 /workspaces/projects：只需 company_id（企业 ID），不需要真人 nick。
        该接口会把企业自身也以 category=organization 返回，这里过滤掉，避免把「企业」
        当成可选项目。返回 [{id, name, pretty_name, company_id}]，失败抛 TapdError。
        """
        if not company_id.strip():
            raise TapdError("请先填写企业 ID（company_id）")
        cid = company_id.strip()
        payload = self._request(
            "GET", "workspaces/projects", params={"company_id": cid}
        )
        projects: list[dict[str, str]] = []
        for row in payload.get("data") or []:
            ws = (row or {}).get("Workspace") or {}
            # 企业自身 category=organization，不是可建单的项目；跳过。
            # 产品线 category=product 也是中间节点，不可建需求，一并跳过。
            if str(ws.get("category") or "").strip() in {"organization", "product"}:
                continue
            if str(ws.get("category") or "").strip() == "organization":
                continue
            pid = str(ws.get("id") or "").strip()
            if not pid:
                continue
            projects.append({
                "id": pid,
                "name": str(ws.get("name") or "").strip(),
                "pretty_name": str(ws.get("pretty_name") or "").strip(),
                # 缓存里要记企业归属，前端企业 ID 下拉从这里提取。
                "company_id": str(ws.get("company_id") or cid).strip(),
            })
        return projects

    @staticmethod
    def _normalise(entity_type: str, obj: dict[str, Any]) -> TapdItem:
        def first(*keys: str) -> str:
            for key in keys:
                if obj.get(key) not in (None, ""):
                    return str(obj[key])
            return ""

        # TAPD stories use `name`; bugs/tasks use `title`.
        title = first("name", "title")
        description = first("description")
        # A git URL is often kept in a custom field; expose common ones.
        git_hint = first("custom_field_git_url", "git_url", "custom_field_one")
        # 父子需求：TAPD 用 parent_id（子→父，顶层为 "0"）和 children_id（父→子，
        # 格式 |id1|id2|，空值 lone "|"）。两者双向冗余，都提取以便交叉校验。
        # story 的父在 parent_id；task 归属的 story 在 story_id。
        # 跳过 "0"（TAPD 顶层哨兵值），继续找下一个候选字段。
        parent_id = ""
        for key in ("parent_id", "story_id"):
            candidate = str(obj.get(key) or "").strip()
            if candidate and candidate != "0":
                parent_id = candidate
                break
        children_raw = first("children_id")
        children_ids = [c.strip() for c in children_raw.split("|") if c.strip()] if children_raw else []
        return TapdItem(
            entity_type=entity_type,
            id=first("id"),
            workspace_id=first("workspace_id"),
            title=title,
            description=description,
            status=first("status", "v_status"),
            owner=first("owner", "current_owner"),
            priority=first("priority_label", "priority"),
            iteration_id=first("iteration_id"),
            created=first("created"),
            modified=first("modified"),
            git_url_hint=git_hint,
            parent_id=parent_id,
            children_ids=children_ids,
            raw=obj,
        )

    # ── write-back ────────────────────────────────────────────────────────────
    def update_status(self, item: TapdItem, status: str) -> bool:
        """Write a status back to TAPD. Returns True on success."""
        if not status:
            return False
        if entity_path := _ENTITIES.get(item.entity_type):
            plural = entity_path[0]
        else:
            raise TapdError(f"不支持的实体类型：{item.entity_type}")
        self._request(
            "POST",
            plural,
            data={
                "id": item.id,
                "workspace_id": item.workspace_id or self.config.primary_workspace_id,
                "status": status,
                "current_user": self.config.current_user,
            },
        )
        logger.info("TAPD %s %s 状态已回填为 %s", item.entity_type, item.id, status)
        return True

    def add_comment(self, item: TapdItem, content: str) -> bool:
        """Post a comment (e.g. the local report path) back to the work item."""
        if not content:
            return False
        path = f"comments?entity_type={item.entity_type}&entity_id={item.id}"
        self._request(
            "POST",
            path,
            data={
                "workspace_id": item.workspace_id or self.config.primary_workspace_id,
                "description": content[:2000],
                "author": self.config.current_user,
            },
        )
        logger.info("TAPD %s %s 评论已回填", item.entity_type, item.id)
        return True
