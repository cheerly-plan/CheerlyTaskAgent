"""HTTP surface: config, import, work items, run/cancel, SSE, reports."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .events import EventBus, sse_frame
from .executor import CodeBuddyExecutor
from .fsmap import FolderBrowser, PathBoundaryError
from .gitmap import GitProjectRegistry
from .projectmap import ProjectMappingStore, ProjectRegistry
from .scheduler import Scheduler, SchedulerSettings
from .sources import EmailSource, ManualSource, RawItem
from .store import TaskStore

logger = logging.getLogger("taskagent")


class ImportBody(BaseModel):
    title: str
    body: str = ""
    source: str = "manual"
    source_ref: str = ""
    git_url_hint: str = ""
    exec_mode: str = "dry-run"
    model: str | None = None


class ImportEmailsBody(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
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
    model: str | None = None
    run_now: bool = False
    approved: bool = False


class RunBody(BaseModel):
    exec_mode: str | None = None
    approved: bool = False
    model: str | None = None


class CommitBody(BaseModel):
    message: str = ""
    dry_run: bool = True  # preview by default; explicit opt-in to actually commit


class CommitRulesBody(BaseModel):
    patterns: list[str] = Field(default_factory=list)


class MapBody(BaseModel):
    repo_name: str


class MappingBody(BaseModel):
    name: str = ""
    aliases: list[str] = []
    path: str = ""
    enabled: bool = True
    note: str = ""


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
    workbuddy_cli_path: str = ""
    dsh_path: str = ""
    default_model: str = ""
    max_concurrent: int = Field(default=1, ge=1)
    auto_sync: bool = True
    imap: dict[str, Any] = Field(default_factory=dict)


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
    browser = FolderBrowser(settings.browse_root)
    events = EventBus()
    from .commitfilter import load_extra_patterns

    executor = CodeBuddyExecutor(
        command=settings.codebuddy_command,
        worktrees_dir=settings.worktrees_dir,
        timeout_s=settings.timeout_s,
        model=settings.codebuddy_model,
        auto_sync=settings.auto_sync,
        backups_dir=settings.data_dir / "sync-backups",
        # Reuse the same ignore rules as commit so we never sync secrets,
        # venvs, data/ or other runtime state back into the main checkout.
        sync_ignore_patterns=tuple(load_extra_patterns(settings.commit_rules_path)),
    )
    scheduler = Scheduler(
        store=store,
        gitmap=gitmap,
        executor=executor,
        events=events,
        settings=SchedulerSettings(
            max_concurrent=settings.max_concurrent, reports_dir=settings.reports_dir
        ),
        projects=projects,
        commit_rules_file=settings.commit_rules_path,
    )
    # A server restart kills in-flight asyncio tasks; reap any work item that
    # was persisted as queued/running so it no longer shows "执行中" forever.
    recovered = scheduler.recover_interrupted()
    if recovered:
        logger.warning("启动恢复：已将 %d 条残留的 queued/running 工作项标记为中断", recovered)

    app = FastAPI(title="CheerlyTaskAgent", version="0.2.0")
    app.state.ctx = SimpleNamespace(
        settings=settings, store=store, gitmap=gitmap, projects=projects,
        browser=browser, events=events, executor=executor, scheduler=scheduler,
    )

    @app.get("/", include_in_schema=False, response_model=None)
    def index() -> HTMLResponse | FileResponse:
        page = settings.web_dir / "index.html"
        if not page.is_file():
            return HTMLResponse("<h1>TaskAgent</h1><p>web/index.html is missing.</p>", 503)
        return FileResponse(page, media_type="text/html")

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {
            "version": "0.2.0",
            "sources": ["manual", "email", "tapd(planned)"],
            "executor": "codebuddy",
            "codebuddy_command": settings.codebuddy_command,
            "max_concurrent": settings.max_concurrent,
            "default_exec_mode": "dry-run",
            "project_map_file": str(
                settings.project_map_file or settings.data_dir / "projects.json"
            ),
            "fs_root": str(settings.browse_root),
            "commit_rules_file": str(settings.commit_rules_path),
            "auto_sync": settings.auto_sync,
            "models": settings.model_choices,
            "default_model": settings.default_model,
        }

    # ── UI-managed settings (系统设置 tab) ────────────────────────────────────
    @app.get("/api/settings")
    def api_get_settings() -> dict[str, Any]:
        return settings.load_ui_settings()

    @app.put("/api/settings")
    def api_put_settings(body: UISettingsBody) -> dict[str, Any]:
        return settings.save_ui_settings(body.model_dump())

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

    @app.post("/api/mappings", status_code=201)
    def api_add_mapping(body: MappingBody) -> dict[str, Any]:
        try:
            return projects.add(body.model_dump()).to_dict()
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/mappings/bulk", status_code=201)
    def api_add_mappings_bulk(body: BulkMappingBody) -> dict[str, Any]:
        added, failed = [], []
        for data in body.items:
            try:
                added.append(projects.add(data.model_dump()).to_dict())
            except ValueError as error:
                failed.append({"item": data.model_dump(), "error": str(error)})
        return {"added": added, "failed": failed}

    @app.put("/api/mappings/{mapping_id}")
    def api_update_mapping(mapping_id: str, body: MappingBody) -> dict[str, Any]:
        try:
            # exclude_unset so a partial update (e.g. toggle enabled) does not
            # wipe other fields with the model's empty defaults.
            return projects.update(mapping_id, body.model_dump(exclude_unset=True)).to_dict()
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.delete("/api/mappings/{mapping_id}")
    def api_delete_mapping(mapping_id: str) -> dict[str, bool]:
        if not projects.remove(mapping_id):
            raise HTTPException(404, "mapping not found")
        return {"deleted": True}

    @app.get("/api/items")
    def api_items(limit: int = 200) -> dict[str, Any]:
        return {"items": [item.summary() for item in store.list(limit=limit)]}

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
                model=body.model,
            )
            if body.run_now:
                item = scheduler.run(
                    item.id, exec_mode=body.exec_mode, approved=body.approved,
                    model=body.model,
                )
            return item.to_dict()
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/import/emails", status_code=201)
    def api_import_emails(body: ImportEmailsBody) -> dict[str, Any]:

        source = EmailSource(body.messages)
        created, skipped = 0, 0
        for raw in source.fetch():
            _, was_created = scheduler.import_raw(raw, exec_mode=body.exec_mode, model=body.model)
            if was_created:
                created += 1
            else:
                skipped += 1
        return {"created": created, "skipped": skipped}

    @app.get("/api/items/{item_id}")
    def api_get_item(item_id: str) -> dict[str, Any]:
        item = store.get(item_id)
        if item is None:
            raise HTTPException(404, "item not found")
        return item.to_dict()

    @app.post("/api/items/{item_id}/map")
    def api_map_item(item_id: str, body: MapBody) -> dict[str, Any]:
        try:
            return scheduler.map_manually(item_id, body.repo_name).to_dict()
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/items/{item_id}/run")
    async def api_run_item(item_id: str, body: RunBody) -> dict[str, Any]:
        # Distinguish "model field omitted" (keep the item's stored choice)
        # from "model: null" (explicitly reset to the global default).
        supplied = "model" in body.model_fields_set
        try:
            return scheduler.run(
                item_id, exec_mode=body.exec_mode, approved=body.approved,
                model=body.model, _model_supplied=supplied,
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
    def api_report(item_id: str) -> FileResponse:
        item = store.get(item_id)
        if item is None or not item.report_path:
            raise HTTPException(404, "report not found")
        path = _safe_under(item.report_path, settings.reports_dir)
        if path is None:
            raise HTTPException(404, "report not found")
        return FileResponse(path, media_type="text/markdown")

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        queue = events.subscribe()

        async def stream():
            try:
                for event in events.replay():
                    yield sse_frame(event)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
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
