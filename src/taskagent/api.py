"""HTTP surface: config, import, work items, run/cancel, SSE, reports."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .automation import AutomationEngine
from .backends import build_registry, describe_backends
from .config import Settings
from .events import EventBus, sse_frame
from .fsmap import FolderBrowser, PathBoundaryError
from .gitmap import GitProjectRegistry
from .projectmap import ProjectMappingStore, ProjectRegistry
from .repoprofile import RepoProfileStore, ensure_profiles
from .report import parent_title_of, render_report
from .review import build_diff, diff_stat
from .route import RouteHistory, route_item
from .scheduler import Scheduler, SchedulerSettings
from .sources import ManualSource, RawItem, parse_raw
from .store import TaskStore
from .tapd import TapdClient, TapdConfig, TapdError, TapdItem, is_closed_status
from .verification import CATEGORIES
from .verifyconfig import (
    ensure_verify_config,
    is_stale,
    load_verify_config,
    prune_verify_config,
)

logger = logging.getLogger("taskagent")


class ImportBody(BaseModel):
    title: str
    body: str = ""
    source: str = "manual"
    source_ref: str = ""
    git_url_hint: str = ""
    exec_mode: str = "dry-run"
    model: str | None = None


class ManualTaskBody(BaseModel):
    title: str
    description: str = ""
    kind: str = "task"
    repo_name: str = ""
    source_ref: str = ""
    git_url_hint: str = ""
    exec_mode: str = "dry-run"
    executor: str = "codebuddy"
    model: str | None = None
    run_now: bool = False
    approved: bool = False


class TapdWorkspacesBody(BaseModel):
    company_id: str = ""


class RunBody(BaseModel):
    exec_mode: str | None = None
    approved: bool = False
    model: str | None = None
    executor: str | None = None


class CommitBody(BaseModel):
    message: str = ""
    dry_run: bool = True  # preview by default; explicit opt-in to actually commit


class CommitRulesBody(BaseModel):
    patterns: list[str] = Field(default_factory=list)


class MapBody(BaseModel):
    repo_name: str


class RepoOverrideBody(BaseModel):
    roles: list[str] = []
    keywords: list[str] = []


class MappingBody(BaseModel):
    name: str = ""
    aliases: list[str] = []
    path: str = ""
    enabled: bool = True
    note: str = ""
    # TAPD 项目绑定：缺了这两个字段，前端提交的绑定会被模型静默丢弃。
    tapd_workspace_ids: list[str] = []
    tapd_workspace_id: str = ""


class BrowseQuery(BaseModel):
    path: str = ""


class ScanBody(BaseModel):
    path: str = ""
    depth: int = 2


class BulkMappingBody(BaseModel):
    items: list[MappingBody] = Field(default_factory=list)


class UISettingsBody(BaseModel):
    """Values edited in the 系统设置 tab; unknown keys are ignored on save."""

    executor: str = "codebuddy"
    codebuddy_command: str = "codebuddy"
    dsh_path: str = ""
    default_model: str = ""
    max_concurrent: int = Field(default=1, ge=1)
    auto_sync: bool = True
    backends: dict[str, Any] = Field(default_factory=dict)
    # 后端 Agent 自己的 LLM 配置（OpenAI 兼容），与执行端 CLI 解耦。
    models: list[dict[str, Any]] = Field(default_factory=list)
    # 后端 Agent 默认使用的模型 id；空值表示第一个启用的模型。
    agent_model: str = ""
    tapd: dict[str, Any] = Field(default_factory=dict)
    # 单一数据源对象：type/name/enabled + 类型专属字段（目前仅 tapd）。
    data_source: dict[str, Any] = Field(default_factory=dict)


class AutoRunBody(BaseModel):
    """任务中心「自动执行」选中的任务 id（只跑勾选的这些）。"""

    ids: list[str] = Field(default_factory=list)


class TapdSyncBody(BaseModel):
    """Manual TAPD sync options (all optional; credentials come from settings)."""

    exec_mode: str | None = None
    modified_since: str = ""
    force: bool = False  # 忽略增量游标，全量重拉


class TapdImportItem(BaseModel):
    """One checked item from the preview, with the user's per-item choices."""

    source_ref: str
    executor: str | None = None
    model: str | None = None
    exec_mode: str | None = None
    repo_name: str | None = None


class TapdImportBody(BaseModel):
    """Confirm the preview: import only the checked items into the task list."""

    items: list[TapdImportItem] = Field(default_factory=list)
    cursor: str = ""  # newest modified from the preview; advanced only on import


def web_not_built_page() -> str:
    """界面尚未构建时的引导页：给出可直接复制的命令，而不是一句 503 文本。"""
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>TaskAgent · 界面未构建</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:#f6f6f9;
         font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
         color:#24263a; }
  main { width:min(580px, calc(100vw - 48px)); background:#fff; border:1px solid #e6e6ee;
         border-radius:14px; padding:28px; box-shadow:0 16px 48px -12px rgb(24 26 48 / .18); }
  h1 { margin:0 0 6px; font-size:17px; letter-spacing:-.01em; }
  p { margin:0 0 14px; font-size:13px; line-height:1.7; color:#5f6273; }
  pre { margin:0 0 14px; padding:12px 14px; background:#f7f7fb; border:1px solid #ededf3;
        border-radius:9px; font-size:12.5px; line-height:1.7; overflow:auto;
        font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  .hint { font-size:12px; color:#9698a9; }
  code { background:#efeffa; color:#4b44ad; padding:1px 5px; border-radius:4px;
         font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; }
</style></head>
<body><main>
  <h1>界面尚未构建</h1>
  <p>后端 API 已就绪，但 <code>web/</code> 的前端产物还不存在。
     前端是 Vite 工程，构建产物不会提交到仓库，需要先执行一次构建：</p>
  <pre>cd web
pnpm install
pnpm build</pre>
  <p>或者用封装好的命令（在仓库根目录执行）：</p>
  <pre>uv run taskagent web-build</pre>
  <p class="hint">构建完成后刷新本页即可。开发者可用 <code>pnpm dev</code> 启动带热更新的前端，
     它会把 <code>/api</code> 代理到 127.0.0.1:8010。</p>
</main></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.worktrees_dir.mkdir(parents=True, exist_ok=True)

    store = TaskStore(settings.tasks_dir)
    gitmap = GitProjectRegistry(list(settings.project_roots))
    gitmap.scan()
    project_store = ProjectMappingStore(
        settings.project_map_file or settings.data_dir / "projects.json",
        seed=settings.project_map_seed,
    )
    projects = ProjectRegistry(project_store)
    # 结构化画像 + 历史学习：多仓库分流用，纯确定性、可解释，不依赖 LLM。
    profile_store = RepoProfileStore(settings.data_dir / "repo-profiles.json")
    route_history = RouteHistory(settings.data_dir / "route-history.json")
    try:
        ensure_profiles(profile_store, projects.list())
    except Exception:  # 画像失败不该挡住启动
        logger.warning("初始化仓库画像失败", exc_info=True)
    # 验证配置与画像同源：启动时给缺配置的仓库补一份（只加不改，手工配置保留）。
    verify_config_path = settings.data_dir / "verify.json"
    try:
        ensure_verify_config(verify_config_path, projects.list())
    except Exception:  # 验证配置失败同样不能挡住启动
        logger.warning("初始化验证配置失败", exc_info=True)
    browser = FolderBrowser(settings.browse_root)
    events = EventBus()

    # 启动时就用「已保存的 UI 设置」而不是环境变量：保存过的并发数/后端配置
    # 重启后应当继续生效，而不是被 env 默认值覆盖。
    initial_ui = settings.load_ui_settings()
    executor_registry = build_registry(settings, ui=initial_ui)
    executor = executor_registry.resolve(executor_registry.default_backend_id)

    def make_tapd_client() -> tuple[TapdClient, dict[str, Any]]:
        """Build a TAPD client from the latest saved UI settings."""
        cfg = settings.load_ui_settings().get("tapd", {})
        tapd_cfg = TapdConfig(
            base_url=str(cfg.get("base_url") or TapdConfig.base_url).strip().rstrip("/"),
            workspace_id=str(cfg.get("workspace_id", "") or "").strip(),
            # 凭证从网页复制时可能带首尾空白/制表符/换行，Basic Auth 会原样编码导致 401。
            api_user=str(cfg.get("api_user", "") or "").strip(),
            api_password=str(cfg.get("api_password", "") or "").strip(),
            webhook_secret=str(cfg.get("webhook_secret", "") or "").strip(),
            entity_types=list(cfg.get("entity_types") or ["story", "bug"]),
            pull_statuses=list(cfg.get("pull_statuses") or []),
            status_on_success=str(cfg.get("status_on_success", "resolved") or "resolved"),
            status_on_failure=str(cfg.get("status_on_failure", "") or ""),
            current_user=str(cfg.get("current_user", "taskagent") or "taskagent"),
        )
        return TapdClient(tapd_cfg), cfg

    def parse_tapd_ref(source_ref: str) -> tuple[str, str] | None:
        """'tapd:story:123' -> ('story', '123')."""
        parts = (source_ref or "").split(":")
        if len(parts) == 3 and parts[0] == "tapd" and parts[1] in {"story", "bug", "task"}:
            return parts[1], parts[2]
        return None

    def backfill_tapd(item: Any, ok: bool) -> None:
        """Write task result back to TAPD (called by scheduler after execution)."""
        cfg = settings.load_ui_settings().get("tapd", {})
        if not cfg.get("enabled"):
            return
        parsed = parse_tapd_ref(item.source_ref)
        if not parsed:
            return
        entity_type, object_id = parsed
        client, _ = make_tapd_client()
        target = TapdItem(
            entity_type=entity_type,
            id=object_id,
            workspace_id=client.config.primary_workspace_id,
            title="", description="", status="", owner="", priority="",
            iteration_id="", created="", modified="",
        )
        status = client.config.status_on_success if ok else client.config.status_on_failure
        if status:
            client.update_status(target, status)
        if ok and item.report_path:
            client.add_comment(target, f"任务已完成，报告：{item.report_path}")

    scheduler = Scheduler(
        store=store,
        gitmap=gitmap,
        executors=executor_registry,
        events=events,
        settings=SchedulerSettings(
            max_concurrent=int(initial_ui.get("max_concurrent") or settings.max_concurrent),
            reports_dir=settings.reports_dir,
            gc_enabled=settings.gc_enabled,
            backup_retention_days=settings.backup_retention_days,
            backup_max_per_task=settings.backup_max_per_task,
        ),
        projects=projects,
        commit_rules_file=settings.commit_rules_path,
        on_finished=backfill_tapd,
    )
    # A server restart kills in-flight asyncio tasks; reap any work item that
    # was persisted as queued/running so it no longer shows "执行中" forever.
    recovered = scheduler.recover_interrupted()
    if recovered:
        logger.warning("启动恢复：已将 %d 条残留的 queued/running 工作项标记为中断", recovered)
    # P0-3: sweep worktrees/backups left by earlier runs so disk use is bounded.
    swept = scheduler.gc_artifacts()
    if swept["worktrees"] or swept["backups"]:
        logger.info(
            "启动清理：移除 worktree %d 个、过期备份 %d 份",
            swept["worktrees"], swept["backups"],
        )
    # 启动时整理一次父子关系：把历史遗留的父需求纠正为容器（不映射、不执行）。
    try:
        scheduler.link_parent_relations()
    except Exception:
        logger.warning("启动父子关系整理失败", exc_info=True)

    # 自动执行引擎：只在两个触发点工作——TAPD 定时拉取、任务中心手动勾选。
    automation = AutomationEngine(
        scheduler=scheduler, store=store, events=events, settings=settings
    )

    # ── TAPD (authoritative source) ────────────────────────────────────────────
    _tapd_last_sync: dict[str, str] = {"modified": ""}  # incremental cursor, in-memory
    # Preview → import handoff. The preview stores the fully-parsed raw items
    # here so the import step never trusts client-supplied titles/descriptions.
    # A nonce ties a preview to its import (stale/duplicate previews are ignored).
    _tapd_preview: dict[str, Any] = {"nonce": "", "raw": {}, "cursor": ""}

    def sync_tapd_now(modified_since: str = "", force: bool = False) -> dict[str, Any]:
        """Pull stories/bugs from TAPD -> parse -> import. Never raises.

        增量同步只拉 ``modified > 游标`` 的条目；游标在进程内维护，所以「本地删除
        工单」不会让 TAPD 重新下发旧数据。``force=True`` 忽略游标做全量重拉。
        """
        client, cfg = make_tapd_client()
        if not client.config.is_configured():
            return {
                "created": 0, "skipped": 0, "archived": 0,
                "error": "TAPD 未配置，缺少：" + ", ".join(client.config.missing()),
            }
        cursor = "" if force else (modified_since or _tapd_last_sync["modified"])
        result = client.fetch_all(modified_since=cursor)
        if result.error:
            return {"created": 0, "skipped": 0, "archived": 0, "error": result.error}
        exec_mode = cfg.get("exec_mode", "dry-run")
        created = skipped = archived = 0
        created_ids: list[str] = []
        for item in result.items:
            # 父需求只是分组信息，不导入成本地任务；子需求靠 parent_title 分组。
            if getattr(item, "children_ids", None):
                skipped += 1
                continue
            raw = RawItem.from_tapd(item)
            # TAPD workspace 绑定的本地项目优先（两处配置统一维护）
            wid = item.workspace_id or client.config.primary_workspace_id
            bound = scheduler.projects.find_all_by_workspace(wid) if scheduler.projects else []
            # 关联一个仓库：直接指定；多个：留待后续按需求/代码决策，不做文本映射猜测。
            repo_name = bound[0].name if len(bound) == 1 else ""
            stage_hint = (
                f"TAPD 项目关联 {len(bound)} 个本地仓库，待确定执行仓库："
                + "、".join(m.name for m in bound)
                if len(bound) > 1 else ""
            )
            try:
                closed = is_closed_status(item.status)
                fresh, was_created = scheduler.import_raw(
                    raw, exec_mode=exec_mode, repo_name=repo_name,
                    auto_map=len(bound) != 1, stage_hint=stage_hint,
                    archived=closed,
                )
                if was_created:
                    if closed:
                        archived += 1
                    else:
                        created += 1
                        created_ids.append(fresh.id)
                else:
                    skipped += 1
            except ValueError as error:
                logger.warning("TAPD 建单失败 %s：%s", item.ref, error)
                skipped += 1
        # 新建/回填完子需求后重算父子关系（父容器标记 + 子列表）。
        _relink_parents()
        # Advance cursor using the newest modified timestamp seen.
        stamps = [i.modified for i in result.items if i.modified]
        if stamps:
            _tapd_last_sync["modified"] = max(stamps)
        return {
            "created": created, "skipped": skipped, "archived": archived,
            "created_ids": created_ids, "error": None,
        }

    def _relink_parents() -> None:
        """同步/导入后重算父子关系（标记父容器 + 回填子列表），失败不影响主流程。"""
        try:
            scheduler.link_parent_relations()
        except Exception as error:  # noqa: BLE001 - 关系绑定是增强，不能拖垮同步
            logger.warning("父子关系绑定失败：%s", error)

    def _tapd_repo_hint(
        item: TapdItem, client: TapdClient
    ) -> tuple[str, str, dict[str, Any]]:
        """Resolve the bound local repo name + stage hint + routing detail.

        多仓库时走 ``route_item``：历史学习 → 文本点名 → 角色契合 → 文本相关度，
        都判不出来就留空并标出候选，等人工确认（确认后会记入历史）。
        """
        wid = item.workspace_id or client.config.primary_workspace_id
        bound = scheduler.projects.find_all_by_workspace(wid) if scheduler.projects else []
        if not bound:
            return "", "", {"repo": "", "method": "none", "reason": "该项目未绑定本地仓库"}
        # 一对一映射：直接用它，不碰画像/历史/LLM，零额外开销。
        if len(bound) == 1:
            return (
                bound[0].name,
                f"已按 TAPD 项目绑定映射：{bound[0].name}",
                {"repo": bound[0].name, "method": "auto", "confidence": 1.0,
                 "reason": "该项目只绑定了一个本地仓库", "needs_confirm": False},
            )
        profiles = {p.mapping_id: p for p in profile_store.all()}
        decision = route_item(
            workspace_id=str(wid),
            title=item.title,
            description=getattr(item, "description", "") or "",
            candidates=bound,
            profiles=profiles,
            history=route_history,
        )
        repo_name = decision.repo
        if decision.needs_confirm:
            names = "、".join(m.name for m in bound)
            hint = f"TAPD 项目绑定 {len(bound)} 个仓库（{names}）｜{decision.reason}"
            repo_name = ""
        else:
            hint = f"自动指定仓库：{decision.repo}（{decision.reason}）"
        return repo_name, hint, decision.to_dict()

    def preview_tapd_now(modified_since: str = "", force: bool = False) -> dict[str, Any]:
        """Fetch TAPD items and diff against existing tasks WITHOUT writing anything.

        Each item is classified as:
          - ``new``       — no task with this source_ref exists yet
          - ``changed``   — a task exists but title/description differ (TAPD was edited)
          - ``unchanged`` — a task exists with identical content (nothing to do)

        Fully-parsed raw items are cached in-process so the later import step
        uses authoritative content, never client-supplied fields. The cursor is
        NOT advanced here — only a confirmed import does that.
        """
        client, _cfg = make_tapd_client()
        if not client.config.is_configured():
            return {"items": [], "cursor": "", "nonce": "", "error":
                    "TAPD 未配置，缺少：" + ", ".join(client.config.missing())}
        cursor = "" if force else (modified_since or _tapd_last_sync["modified"])
        result = client.fetch_all(modified_since=cursor)
        if result.error:
            return {"items": [], "cursor": "", "nonce": "", "error": result.error}

        # Index existing tasks by source_ref (= dedupe_key for TAPD items).
        existing: dict[str, Any] = {}
        for task in scheduler.store.list(limit=100000):
            if task.source_ref:
                existing[task.source_ref] = task

        raw_cache: dict[str, dict[str, Any]] = {}
        previews: list[dict[str, Any]] = []
        # 父子关系：子的 parent_ref 指向父的 source_ref；据此标出本次预览里的父容器。
        parent_refs = {
            ref for ref in (
                getattr(item, "parent_ref", "") or "" for item in result.items
            ) if ref
        }
        # 父标题索引：TAPD 不保证返回 parent_name，用本次拉到的父需求标题补齐。
        title_by_ref = {item.ref: item.title for item in result.items if item.ref}
        for item in result.items:
            # 父需求不出现在预览/导入列表里。
            if getattr(item, "children_ids", None):
                continue
            raw = RawItem.from_tapd(item)
            if raw.parent_ref and not raw.parent_title:
                raw.parent_title = title_by_ref.get(raw.parent_ref, "")
            probe = parse_raw(raw, exec_mode="dry-run")  # content only; choices applied at import
            old = existing.get(raw.source_ref)
            changes: list[str] = []
            if old is not None:
                if old.title != probe.title:
                    changes.append("title")
                if old.description != probe.description:
                    changes.append("description")
            status = "new" if old is None else ("changed" if changes else "unchanged")
            repo_name, stage_hint, route_info = _tapd_repo_hint(item, client)
            raw_cache[raw.source_ref] = {
                "raw": raw,
                "status": status,
                "repo_name": repo_name,
                "stage_hint": stage_hint,
                "route": route_info,
                "modified": item.modified,
                "existing_id": old.id if old is not None else "",
                "source_status": item.status,
            }
            previews.append({
                "source_ref": raw.source_ref,
                "status": status,
                "archived": is_closed_status(item.status),
                "title": probe.title,
                "description": probe.description,
                "kind": probe.kind.value if hasattr(probe.kind, "value") else str(probe.kind),
                "external_url": item.external_url,
                "modified": item.modified,
                "repo_name": repo_name,
                "route": route_info,
                "changes": changes,
                "existing_id": old.id if old is not None else "",
                "parent_ref": raw.parent_ref,
                "is_container": bool(raw.source_ref) and raw.source_ref in parent_refs,
            })

        stamps = [i.modified for i in result.items if i.modified]
        nonce = secrets.token_hex(8)
        _tapd_preview.update({"nonce": nonce, "raw": raw_cache, "cursor": max(stamps) if stamps else ""})
        return {"items": previews, "cursor": _tapd_preview["cursor"], "nonce": nonce, "error": None}

    def import_tapd_now(body: TapdImportBody) -> dict[str, Any]:
        """Import only the checked preview items; advance cursor to the imported ones.

        Uses cached authoritative raw items keyed by source_ref. Unchecked items
        are left for a later sync: the cursor only moves up to the newest modified
        among the items actually imported, so nothing is silently dropped.
        """
        if not body.items:
            return {"created": 0, "updated": 0, "skipped": 0, "archived": 0, "error": None}
        if not _tapd_preview["nonce"]:
            return {
                "created": 0, "updated": 0, "skipped": 0, "archived": 0,
                "error": "预览已失效，请重新同步",
            }
        from .models import ARCHIVED_SOURCE_STAGE, ItemStatus

        created = updated = skipped = archived = 0
        imported_stamps: list[str] = []
        for sel in body.items:
            cached = _tapd_preview["raw"].get(sel.source_ref)
            if not cached:
                skipped += 1
                continue
            raw: RawItem = cached["raw"]
            exec_mode = sel.exec_mode or "dry-run"
            if exec_mode not in {"dry-run", "apply"}:
                exec_mode = "dry-run"
            repo_name = sel.repo_name if sel.repo_name is not None else cached["repo_name"]
            closed = is_closed_status(cached.get("source_status", ""))
            try:
                if cached["status"] == "changed" and cached["existing_id"]:
                    # Update in place: keep status/results/history, refresh content only.
                    old = scheduler.store.get(cached["existing_id"])
                    if old is None:
                        skipped += 1
                        continue
                    probe = parse_raw(raw, exec_mode=exec_mode)
                    old.title = probe.title
                    old.description = probe.description
                    if sel.executor:
                        old.executor = sel.executor
                    if sel.model is not None:
                        old.model = sel.model or None
                    if repo_name:
                        resolved = scheduler.projects.resolve_name(repo_name) if scheduler.projects else None
                        if resolved:
                            old.repo = resolved
                    if closed:
                        old.status = ItemStatus.SKIPPED
                        old.exec_mode = "apply"
                        old.stage = ARCHIVED_SOURCE_STAGE
                    elif old.status == ItemStatus.SKIPPED and old.stage == ARCHIVED_SOURCE_STAGE:
                        # 来源从已关闭回到活跃：复活到任务中心。
                        old.status = ItemStatus.DISCOVERED
                        old.exec_mode = exec_mode
                        old.stage = ""
                        if repo_name:
                            resolved = scheduler.projects.resolve_name(repo_name) if scheduler.projects else None
                            if resolved:
                                old.repo = resolved
                                old.status = ItemStatus.READY
                    old.touch()
                    scheduler.store.save(old)
                    updated += 1
                else:
                    _, was_created = scheduler.import_raw(
                        raw, exec_mode=exec_mode, repo_name=repo_name,
                        executor=sel.executor, model=sel.model,
                        stage_hint=cached["stage_hint"],
                        auto_map=not repo_name, archived=closed,
                    )
                    if was_created:
                        if closed:
                            archived += 1
                        else:
                            created += 1
                if cached.get("modified"):
                    imported_stamps.append(cached["modified"])
            except ValueError as error:
                logger.warning("TAPD 导入失败 %s：%s", sel.source_ref, error)
                skipped += 1
        # Only advance as far as the latest item we actually imported.
        if imported_stamps:
            newest_imported = max(imported_stamps)
            final_cursor = min(newest_imported, body.cursor) if body.cursor else newest_imported
            _tapd_last_sync["modified"] = max(_tapd_last_sync["modified"], final_cursor)
        _relink_parents()
        _tapd_preview.update({"nonce": "", "raw": {}, "cursor": ""})
        return {
            "created": created, "updated": updated, "skipped": skipped,
            "archived": archived, "error": None,
        }

    def tapd_webhook_sync(event: str, object_id: str) -> dict[str, Any]:
        """A webhook carries only an id — always pull full details from TAPD."""
        # Determine entity type from the webhook event name (story::create, bug::status_change…)
        entity_type = event.split("::", 1)[0] if "::" in event else ""
        if entity_type not in {"story", "bug", "task"}:
            return {"created": 0, "skipped": 0, "archived": 0, "error": f"无法识别的 Webhook 事件：{event}"}
        client, cfg = make_tapd_client()
        if not client.config.is_configured():
            return {
                "created": 0, "skipped": 0, "archived": 0,
                "error": "TAPD 未配置，缺少：" + ", ".join(client.config.missing()),
            }
        try:
            item = client.fetch_one(entity_type, object_id)
        except TapdError as error:
            return {"created": 0, "skipped": 0, "archived": 0, "error": str(error)}
        # Webhook 也可能推送父需求事件；同样不建单。
        if getattr(item, "children_ids", None):
            return {"created": 0, "skipped": 1, "archived": 0, "ignored": True}
        raw = RawItem.from_tapd(item)
        wid = item.workspace_id or client.config.primary_workspace_id
        bound = scheduler.projects.find_all_by_workspace(wid) if scheduler.projects else []
        repo_name = bound[0].name if len(bound) == 1 else ""
        stage_hint = (
            f"TAPD 项目关联 {len(bound)} 个本地仓库，待确定执行仓库："
            + "、".join(m.name for m in bound)
            if len(bound) > 1 else ""
        )
        closed = is_closed_status(item.status)
        try:
            _, was_created = scheduler.import_raw(
                raw, exec_mode=cfg.get("exec_mode", "dry-run"),
                repo_name=repo_name, auto_map=len(bound) != 1, stage_hint=stage_hint,
                archived=closed,
            )
        except ValueError as error:
            return {"created": 0, "skipped": 0, "archived": 0, "error": str(error)}
        _relink_parents()
        return {
            "created": 0 if closed else (1 if was_created else 0),
            "skipped": 0 if was_created else 1,
            "archived": 1 if (was_created and closed) else 0,
            "error": None,
        }

    async def tapd_poll_loop() -> None:
        """Periodically pull from TAPD while auto_pull is on; re-reads settings each tick."""
        while True:
            cfg = settings.load_ui_settings().get("tapd", {})
            interval_s = max(1, int(cfg.get("interval_minutes", 5) or 5)) * 60
            if cfg.get("enabled") and cfg.get("auto_pull"):
                result = await asyncio.to_thread(sync_tapd_now)
                if result.get("error"):
                    logger.warning("定时同步 TAPD 失败：%s", result["error"])
                elif result.get("created") or result.get("archived"):
                    logger.info(
                        "定时同步 TAPD：新建 %d 条，归档 %d 条",
                        result["created"], result.get("archived", 0),
                    )
                    # 定时拉取的数据直接自动执行；手动「立即同步」不自动跑。
                    created_ids = result.get("created_ids") or []
                    if created_ids:
                        await automation.dispatch(created_ids, trigger="tapd-poll")
            await asyncio.sleep(interval_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tapd_task = asyncio.create_task(tapd_poll_loop(), name="tapd-poll")
        try:
            yield
        finally:
            logger.info("关闭中：断开 SSE、取消后台任务…")
            tapd_task.cancel()
            await asyncio.gather(tapd_task, return_exceptions=True)
            events.close()
            await scheduler.shutdown()

    app = FastAPI(title="CheerlyTaskAgent", version="0.2.0", lifespan=lifespan)
    app.state.ctx = SimpleNamespace(
        settings=settings, store=store, gitmap=gitmap, projects=projects,
        browser=browser, events=events, executor=executor, executors=executor_registry,
        scheduler=scheduler,
    )

    # 前端由 `web/` 的 Vite 工程构建到 web_dir；产物不入仓，因此首次运行需先 build。
    assets_dir = settings.web_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False, response_model=None)
    def index() -> HTMLResponse | FileResponse:
        page = settings.web_dir / "index.html"
        if not page.is_file():
            return HTMLResponse(web_not_built_page(), status_code=503)
        return FileResponse(page, media_type="text/html")

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        # Model list is discovered live from the default backend's CLI, not
        # hard-coded, so the UI always matches the installed CLI. Fall back to
        # an empty list when no backend is configured or discovery fails.
        discovered: list[str] = []
        default_backend = executor_registry.resolve(executor_registry.default_backend_id)
        if default_backend is not None:
            try:
                discovered = default_backend.list_models()
            except (OSError, ValueError, RuntimeError):  # discovery is best-effort
                discovered = []
        # The default model is the default backend's own ``model`` field —
        # "global default 选了哪个就回填哪个".
        default_model = (default_backend.model if default_backend is not None else "") or ""
        return {
            "version": "0.2.0",
            "sources": ["manual", "tapd"],
            "executor": executor_registry.default_backend_id or "codebuddy",
            "backends": describe_backends(settings, executor_registry),
            "codebuddy_command": settings.codebuddy_command,
            "max_concurrent": scheduler.settings.max_concurrent,
            "default_exec_mode": "dry-run",
            "project_map_file": str(
                settings.project_map_file or settings.data_dir / "projects.json"
            ),
            "fs_root": str(settings.browse_root),
            "commit_rules_file": str(settings.commit_rules_path),
            "auto_sync": settings.auto_sync,
            "models": settings.model_choices(discovered),
            "default_model": default_model,
        }

    # ── UI-managed settings (系统设置 tab) ────────────────────────────────────
    @app.get("/api/settings")
    def api_get_settings() -> dict[str, Any]:
        return settings.load_ui_settings()

    def apply_runtime_settings() -> None:
        """Rebuild executors and concurrency from the saved UI settings.

        Everything the 系统设置 tab can edit is applied here, so saving no
        longer requires a restart. In-flight runs keep the executor they
        started with; new runs pick up the fresh registry.
        """
        nonlocal executor_registry, executor
        ui = settings.load_ui_settings()
        executor_registry = build_registry(settings, ui=ui)
        executor = executor_registry.resolve(executor_registry.default_backend_id)
        app.state.ctx.executor = executor
        app.state.ctx.executors = executor_registry
        scheduler.reconfigure(
            executors=executor_registry,
            max_concurrent=int(ui.get("max_concurrent") or 1),
        )

    @app.put("/api/settings")
    def api_put_settings(body: UISettingsBody) -> dict[str, Any]:
        # exclude_unset: fields the client didn't send (e.g. ``backends`` from
        # an older client) must not be treated as "clear everything".
        saved = settings.save_ui_settings(body.model_dump(exclude_unset=True))
        apply_runtime_settings()
        return saved

    @app.post("/api/backends/{backend_id}/probe")
    def api_probe_backend(backend_id: str) -> dict[str, Any]:
        """Verify on demand whether a backend can honour a per-task model.

        Kept out of /api/config so page loads never spawn extra CLI processes:
        the UI calls this the first time the user opens a model picker.
        """
        executor = executor_registry.get(backend_id)
        if executor is None:
            raise HTTPException(404, f"backend not found: {backend_id}")
        check = getattr(executor, "supports_model_selection_check", None)
        try:
            capable = bool(check()) if callable(check) else bool(
                getattr(executor, "supports_model_selection", True)
            )
            deep = getattr(executor, "list_models_deep", None)
            models = deep() if callable(deep) else executor.list_models()
        except (OSError, ValueError, RuntimeError) as error:  # never 500 the UI
            logger.warning("探测后端 %s 失败：%s", backend_id, error)
            return {"id": backend_id, "model_selection": False, "models": [], "error": str(error)}
        return {"id": backend_id, "model_selection": capable, "models": models}

    # ── read-only folder browser + on-demand scanner ────────────────────────
    @app.get("/api/fs/browse")
    def api_fs_browse(path: str = "") -> dict[str, Any]:
        try:
            return browser.browse(path)
        except (ValueError, PathBoundaryError) as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/fs/scan")
    def api_fs_scan(body: ScanBody) -> dict[str, Any]:
        try:
            return browser.scan(body.path, depth=body.depth)
        except (ValueError, PathBoundaryError) as error:
            raise HTTPException(400, str(error)) from error

    # ── project name/alias -> folder mapping CRUD ──────────────────────────────
    @app.get("/api/mappings")
    def api_list_mappings() -> dict[str, Any]:
        return {"mappings": [m.to_dict() for m in projects.list()]}

    def _refresh_profile(mapping: Any) -> None:
        """新增/修改映射后就地建立仓库画像，供多仓库分流使用。"""
        if mapping.enabled:
            try:
                profile_store.rebuild(mapping.id, mapping.name, mapping.path)
            except Exception:
                logger.warning("建立仓库画像失败：%s", mapping.path, exc_info=True)

    def _refresh_verify(mapping: Any) -> None:
        """新增/修改映射后补生成验证配置；已有条目保持不变。"""
        try:
            ensure_verify_config(verify_config_path, [mapping], only={mapping.name})
            prune_verify_config(
                verify_config_path, {item.name for item in projects.list()}
            )
        except Exception:
            logger.warning("生成验证配置失败：%s", mapping.path, exc_info=True)

    def _verify_rows() -> list[dict[str, Any]]:
        data = load_verify_config(verify_config_path)
        repos = data.get("repos") if isinstance(data.get("repos"), dict) else {}
        rows: list[dict[str, Any]] = []
        for mapping in projects.list():
            entry = repos.get(mapping.name)
            entry = entry if isinstance(entry, dict) else {}
            checks = entry.get("checks") if isinstance(entry.get("checks"), list) else []
            raw_na = entry.get("na") if isinstance(entry.get("na"), dict) else {}
            na = {
                str(key): str(value) for key, value in raw_na.items()
                if str(value).strip()
            }
            present = {str(check.get("category")) for check in checks}
            missing = [
                category for category in CATEGORIES
                if category not in present and category not in na
            ]
            rows.append({
                "mapping_id": mapping.id,
                "name": mapping.name,
                "path": mapping.path,
                "checks": checks,
                "na": na,
                "missing_categories": missing,
                "configured": bool(checks),
                "complete": bool(checks) and not missing,
                "stale": is_stale(entry, mapping.path) if entry else False,
                "generated_at": str(entry.get("generated_at") or ""),
                "source": str(entry.get("source") or ""),
            })
        return rows

    @app.post("/api/mappings", status_code=201)
    def api_add_mapping(body: MappingBody) -> dict[str, Any]:
        try:
            mapping = projects.add(body.model_dump())
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        _refresh_profile(mapping)
        _refresh_verify(mapping)
        return mapping.to_dict()

    @app.post("/api/mappings/bulk", status_code=201)
    def api_add_mappings_bulk(body: BulkMappingBody) -> dict[str, Any]:
        added, failed = [], []
        for data in body.items:
            try:
                mapping = projects.add(data.model_dump())
                _refresh_profile(mapping)
                _refresh_verify(mapping)
                added.append(mapping.to_dict())
            except ValueError as error:
                failed.append({"item": data.model_dump(), "error": str(error)})
        return {"added": added, "failed": failed}

    @app.put("/api/mappings/{mapping_id}")
    def api_update_mapping(mapping_id: str, body: MappingBody) -> dict[str, Any]:
        try:
            # exclude_unset so a partial update (e.g. toggle enabled) does not
            # wipe other fields with the model's empty defaults.
            updated = projects.update(mapping_id, body.model_dump(exclude_unset=True))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        _refresh_profile(updated)
        _refresh_verify(updated)
        return updated.to_dict()

    @app.delete("/api/mappings/{mapping_id}")
    def api_delete_mapping(mapping_id: str) -> dict[str, bool]:
        if not projects.remove(mapping_id):
            raise HTTPException(404, "mapping not found")
        profile_store.remove(mapping_id)
        prune_verify_config(
            verify_config_path, {item.name for item in projects.list()}
        )
        return {"deleted": True}

    @app.get("/api/items")
    def api_items(limit: int = 200, offset: int = 0) -> dict[str, Any]:
        # Clamp: the UI paginates locally over a bounded window, and an
        # unbounded limit would let one request load the whole store.
        safe_limit = max(1, min(2000, limit))
        return {
            "items": [item.summary() for item in store.list(limit=safe_limit, offset=offset)],
            "total": store.count(),
            "limit": safe_limit,
            "offset": max(0, offset),
        }

    # ── 自动合并：冲突任务重试合并 / 查看 diff ─────────────────────────────
    @app.post("/api/items/{item_id}/merge")
    async def api_merge_item(item_id: str) -> dict[str, Any]:
        """重试原子合并：无冲突才写回主仓库工作区（不 commit）。"""
        try:
            item = await asyncio.to_thread(scheduler.merge_item, item_id)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return item.to_dict()

    @app.get("/api/items/{item_id}/diff")
    def api_item_diff(item_id: str) -> dict[str, Any]:
        item = store.get(item_id)
        if item is None or item.result is None:
            raise HTTPException(404, "任务或执行结果不存在")
        worktree = item.result.worktree or ""
        changed = list(item.result.changed_files)
        return {
            "diff": build_diff(worktree, changed),
            "stat": diff_stat(worktree, changed),
            "worktree": worktree,
        }

    @app.post("/api/tasks/auto-run")
    async def api_auto_run(body: AutoRunBody) -> dict[str, Any]:
        """任务中心手动触发：只自动执行用户勾选的任务。"""
        ids = [str(i).strip() for i in body.ids if str(i).strip()]
        if not ids:
            raise HTTPException(400, "至少选择一个任务")
        return await automation.dispatch(ids, trigger="manual")

    @app.post("/api/import", status_code=201)
    def api_import(body: ImportBody) -> dict[str, Any]:
        from .models import SourceType

        raw = RawItem(
            source=SourceType(body.source),
            title=body.title,
            body=body.body,
            source_ref=body.source_ref,
            git_url_hint=body.git_url_hint,
        )
        try:
            source = ManualSource([raw])
            item = None
            for raw_item in source.fetch():
                item, _ = scheduler.import_raw(raw_item, exec_mode=body.exec_mode, model=body.model)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return item.to_dict()  # type: ignore[union-attr]

    @app.post("/api/tasks/manual", status_code=201)
    async def api_create_manual_task(body: ManualTaskBody) -> dict[str, Any]:
        """Create a work item from direct user input and optionally run it."""
        try:
            item = scheduler.create_manual(
                title=body.title,
                description=body.description,
                kind=body.kind,
                repo_name=body.repo_name,
                source_ref=body.source_ref,
                git_url_hint=body.git_url_hint,
                exec_mode=body.exec_mode,
                executor=body.executor,
                model=body.model,
            )
            if body.run_now:
                item = scheduler.run(
                    item.id, exec_mode=body.exec_mode, approved=body.approved,
                    executor=body.executor, model=body.model,
                )
            return item.to_dict()
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/tapd/workspaces")
    async def api_tapd_workspaces(body: TapdWorkspacesBody) -> dict[str, Any]:
        """列出企业下的 TAPD 项目，供数据源页选择 workspace_id。

        company_id 从请求体取（也回退到已保存配置），凭证用当前保存的应用凭证。
        """
        cfg = settings.load_ui_settings().get("tapd", {})
        company_id = (body.company_id or "").strip() or str(cfg.get("company_id", "") or "").strip()
        client, _ = make_tapd_client()
        try:
            projects = await asyncio.to_thread(client.list_projects, company_id)
        except TapdError as error:
            raise HTTPException(400, str(error)) from error
        return {"projects": projects}

    @app.post("/api/tapd/sync")
    async def api_tapd_sync(body: TapdSyncBody | None = None) -> dict[str, Any]:
        """Manually pull stories/bugs from TAPD and create work items."""
        result = await asyncio.to_thread(
            sync_tapd_now,
            (body.modified_since if body else "") or "",
            bool(body.force) if body else False,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return {
            "created": result["created"], "skipped": result["skipped"],
            "archived": result.get("archived", 0),
        }

    @app.post("/api/tapd/preview")
    async def api_tapd_preview(body: TapdSyncBody | None = None) -> dict[str, Any]:
        """Preview TAPD changes (new/changed/unchanged) without touching tasks."""
        result = await asyncio.to_thread(
            preview_tapd_now,
            (body.modified_since if body else "") or "",
            bool(body.force) if body else False,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return {
            "items": result["items"],
            "cursor": result["cursor"],
            "nonce": result["nonce"],
        }

    @app.post("/api/tapd/import")
    async def api_tapd_import(body: TapdImportBody) -> dict[str, Any]:
        """Import only the checked preview items into the task list."""
        result = await asyncio.to_thread(import_tapd_now, body)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return {
            "created": result["created"], "updated": result["updated"],
            "skipped": result["skipped"], "archived": result.get("archived", 0),
        }

    @app.post("/api/tapd/webhook")
    async def api_tapd_webhook(request: Request) -> dict[str, Any]:
        """Receive TAPD webhook events and sync the referenced work item.

        Accepts both JSON and form payloads. An optional shared secret (set in
        TAPD webhook config) is verified against the `secret` field to reject
        forged requests.
        """
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = await request.json()
            except ValueError as error:
                raise HTTPException(400, f"无效 JSON：{error}") from error
        else:
            form = await request.form()
            payload = dict(form)
        if not isinstance(payload, dict):
            raise HTTPException(400, "Webhook payload 格式错误")

        cfg = settings.load_ui_settings().get("tapd", {})
        expected_secret = str(cfg.get("webhook_secret", "") or "")
        if expected_secret and str(payload.get("secret", "")) != expected_secret:
            raise HTTPException(403, "Webhook secret 校验失败")

        event = str(payload.get("event", ""))
        object_id = str(payload.get("id", ""))
        if not event or not object_id:
            raise HTTPException(400, "Webhook 缺少 event 或 id")
        # Ignore delete events; there is no work item to sync.
        if "::delete" in event:
            return {"created": 0, "skipped": 0, "archived": 0, "ignored": True}
        result = await asyncio.to_thread(tapd_webhook_sync, event, object_id)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return {
            "created": result["created"], "skipped": result["skipped"],
            "archived": result.get("archived", 0),
        }

    @app.get("/api/repo-profiles")
    def api_repo_profiles() -> dict[str, Any]:
        """结构化画像：每个本地仓库的角色、依赖指纹、关键词等（多仓库分流依据）。"""
        return {"profiles": [p.to_dict() for p in profile_store.all()]}

    @app.post("/api/repo-profiles/rebuild")
    def api_repo_profiles_rebuild() -> dict[str, Any]:
        """重建全部画像（仓库有改动时可手动触发）。"""
        result = ensure_profiles(profile_store, projects.list(), force=True)
        return {**result, "profiles": [p.to_dict() for p in profile_store.all()]}

    @app.get("/api/verify-config")
    def api_verify_config() -> dict[str, Any]:
        """自动生成的验证配置：每个仓库的检查、是否配置、仓库文件是否已变化。"""
        return {"repos": _verify_rows(), "path": str(verify_config_path)}

    @app.post("/api/verify-config/rebuild")
    def api_verify_config_rebuild() -> dict[str, Any]:
        """按仓库文件重建全部验证配置（手工触发；已有配置会被覆盖）。"""
        result = ensure_verify_config(
            verify_config_path, projects.list(), force=True, prune=True
        )
        return {
            "created": result["created"],
            "rebuilt": result["rebuilt"],
            "pruned": result["pruned"],
            "repos": _verify_rows(),
        }

    @app.post("/api/repo-profiles/{mapping_id}/override")
    def api_repo_profile_override(mapping_id: str, body: RepoOverrideBody) -> dict[str, Any]:
        """人工覆盖角色 / 关键词；覆盖优先于自动推断。"""
        profile = profile_store.get(mapping_id)
        if profile is None:
            profile_store.rebuild(
                mapping_id,
                next((m.name for m in projects.list() if m.id == mapping_id), mapping_id),
                next((m.path for m in projects.list() if m.id == mapping_id), ""),
            )
            profile = profile_store.get(mapping_id)
        if profile is None:
            raise HTTPException(404, "profile not found")
        profile.override_roles = [r for r in body.roles if r]
        profile.override_keywords = [k for k in body.keywords if k]
        profile_store.save()
        return profile.to_dict()

    @app.post("/api/items/auto-map")
    def api_auto_map() -> dict[str, Any]:
        """按 TAPD 项目绑定，把未映射的历史工单补上仓库。

        新的导入已经会按绑定直接映射；这个端点解决「先建单、后补绑定」的存量
        数据。同一 TAPD 项目绑定多个仓库时无法自动选择，留给人工确认。
        """
        from .models import ItemStatus, SourceType

        def workspace_of(item: Any) -> str:
            if item.tapd_workspace_id:
                return item.tapd_workspace_id
            # 历史数据没有记录 workspace：TAPD story ID 形如 "11" + workspace + 序号，
            # 用已知绑定做前缀匹配，避免猜 ID 长度。
            match = re.match(r"^tapd:[a-z]+:(\d+)$", item.source_ref or "")
            if not match:
                return ""
            digits = match.group(1)
            for mapping in projects.list():
                for wid in mapping.tapd_workspace_ids:
                    if wid and digits.startswith("11" + str(wid)):
                        return str(wid)
            return ""

        mapped = ambiguous = unmatched = 0
        for item in store.list(limit=2000):
            if item.repo is not None or item.source is not SourceType.TAPD:
                continue
            bound = projects.find_all_by_workspace(workspace_of(item))
            if len(bound) == 1:
                item.repo = bound[0].to_local_repo()
                item.status = ItemStatus.READY
                item.stage = f"已按 TAPD 项目绑定映射：{bound[0].name}"
                item.error = None
                store.save(item)
                mapped += 1
            elif len(bound) > 1:
                ambiguous += 1
            else:
                unmatched += 1
        return {"mapped": mapped, "ambiguous": ambiguous, "unmatched": unmatched}

    @app.get("/api/items/{item_id}")
    def api_get_item(item_id: str) -> dict[str, Any]:
        item = store.get(item_id)
        if item is None:
            raise HTTPException(404, "item not found")
        return item.to_dict()

    @app.post("/api/items/{item_id}/map")
    def api_map_item(item_id: str, body: MapBody) -> dict[str, Any]:
        try:
            mapped = scheduler.map_manually(item_id, body.repo_name)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        # 人工指定仓库 = 最可靠的路由证据，记入历史供同类需求复用。
        if mapped.tapd_workspace_id:
            try:
                route_history.record(
                    str(mapped.tapd_workspace_id),
                    f"{mapped.title} {mapped.description or ''}",
                    mapped.repo.name if mapped.repo else body.repo_name,
                )
            except Exception:
                logger.warning("记录路由历史失败", exc_info=True)
        return mapped.to_dict()

    @app.post("/api/items/{item_id}/run")
    async def api_run_item(item_id: str, body: RunBody) -> dict[str, Any]:
        # Distinguish "model field omitted" (keep the item's stored choice)
        # from "model: null" (explicitly reset to the global default).
        supplied = "model" in body.model_fields_set
        try:
            return scheduler.run(
                item_id, exec_mode=body.exec_mode, approved=body.approved,
                executor=body.executor, model=body.model, _model_supplied=supplied,
            ).summary()
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/items/{item_id}/cancel")
    def api_cancel_item(item_id: str) -> dict[str, Any]:
        try:
            return {"cancelled": scheduler.cancel(item_id)}
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/api/commit/rules")
    def api_get_commit_rules() -> dict[str, Any]:
        from .commitfilter import DEFAULT_IGNORE, load_extra_patterns

        return {
            "default": list(DEFAULT_IGNORE),
            "extra": load_extra_patterns(settings.commit_rules_path),
            "rules_file": str(settings.commit_rules_path),
        }

    @app.put("/api/commit/rules")
    def api_put_commit_rules(body: CommitRulesBody) -> dict[str, Any]:
        from .commitfilter import save_extra_patterns

        try:
            save_extra_patterns(settings.commit_rules_path, body.patterns)
        except OSError as error:
            raise HTTPException(400, f"保存过滤规则失败：{error}") from error
        return {"ok": True, "extra": body.patterns}

    @app.get("/api/items/{item_id}/commit/preview")
    def api_commit_preview(item_id: str) -> dict[str, Any]:
        try:
            return scheduler.commit_preview(item_id)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/items/{item_id}/commit")
    def api_commit_item(item_id: str, body: CommitBody) -> dict[str, Any]:
        try:
            if body.dry_run:
                return {"dry_run": True, **scheduler.commit_preview(item_id), "message": body.message}
            result = scheduler.commit(item_id, message=body.message)
            return {"dry_run": False, **result.to_dict()}
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.delete("/api/items/{item_id}")
    def api_delete_item(item_id: str) -> dict[str, bool]:
        item = store.get(item_id)
        if item is None:
            raise HTTPException(404, "item not found")
        if not item.is_terminal():
            scheduler.cancel(item_id)
        path = settings.tasks_dir / f"{item_id}.json"
        if path.is_file():
            path.unlink()
        return {"deleted": True}

    @app.get("/api/items/{item_id}/report")
    def api_report(item_id: str) -> Response:
        item = store.get(item_id)
        if item is None or not item.report_path:
            raise HTTPException(404, "report not found")
        path = _safe_under(item.report_path, settings.reports_dir)
        if path is None:
            raise HTTPException(404, "report not found")
        # Render from live item data so legacy reports (which embedded the
        # entire raw codebuddy stdout) are compact too. The on-disk .md remains
        # an untouched historical archive; downloads get this compact version.
        return Response(
            render_report(item, parent_title_of(item, store)),
            media_type="text/markdown; charset=utf-8",
        )

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        queue = events.subscribe()

        async def stream():
            try:
                for event in events.replay():
                    yield sse_frame(event, replay=True)
                while not events.closed:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:  # shutdown sentinel from EventBus.close()
                        break
                    yield sse_frame(event)
            finally:
                events.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _safe_under(path_str: str, root) -> Any:
    from pathlib import Path

    target = Path(path_str).resolve()
    base = Path(root).resolve()
    return target if base in target.parents and target.is_file() else None
