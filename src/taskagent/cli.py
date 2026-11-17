"""CLI: serve the UI, scan repos, import emails, or run an offline demo."""

from __future__ import annotations

import argparse
import asyncio

from .config import Settings
from .events import EventBus
from .executor import CodeBuddyExecutor
from .gitmap import GitProjectRegistry
from .projectmap import ProjectMappingStore, ProjectRegistry
from .scheduler import Scheduler, SchedulerSettings
from .sources import RawItem
from .store import TaskStore


def _build(settings: Settings):
    store = TaskStore(settings.tasks_dir)
    gitmap = GitProjectRegistry(list(settings.project_roots))
    gitmap.scan()
    project_store = ProjectMappingStore(
        settings.project_map_file or settings.data_dir / "projects.json",
        seed=settings.project_map_seed,
    )
    projects = ProjectRegistry(project_store)
    events = EventBus()
    executor = CodeBuddyExecutor(
        command=settings.codebuddy_command,
        worktrees_dir=settings.worktrees_dir,
        timeout_s=settings.timeout_s,
        model=settings.codebuddy_model,
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
    )
    return store, gitmap, projects, events, executor, scheduler


def serve(settings: Settings) -> None:
    import uvicorn

    print(f"TaskAgent → http://{settings.host}:{settings.port}")
    uvicorn.run("taskagent.api:create_app", host=settings.host, port=settings.port, factory=True)


def scan(settings: Settings) -> None:
    gitmap = GitProjectRegistry(list(settings.project_roots))
    repos = gitmap.scan()
    print(f"扫描到 {len(repos)} 个本地 git 仓库：\n")
    for repo in repos:
        print(f"  {repo.name:24} {repo.origin:60} → {repo.path}")
    if not repos:
        print("  未找到。用 TASKAGENT_PROJECT_ROOTS 指定搜索根目录（冒号分隔）。")


async def demo(settings: Settings) -> None:
    """Offline: sample emails -> map -> dry-run execute -> report."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store, gitmap, _projects, _events, _executor, scheduler = _build(settings)
    print(f"映射仓库：{len(gitmap.list())} 个\n")

    sample_emails = [
        {
            "id": "demo-msg-001",
            "subject": "【BUG】登录接口偶发 500 - cheerly-plan/CheerlyAgent",
            "body": (
                "线上登录偶发返回 500，复现步骤：\n"
                "1. 连续快速点击登录按钮\n"
                "2. 部分请求返回 500\n"
                "仓库：https://github.com/cheerly-plan/CheerlyAgent.git\n\n"
                "麻烦排查并修复。"
            ),
        },
        {
            "id": "demo-msg-002",
            "subject": "【需求】为 CheerlyHarness 增加 Markdown 渲染",
            "body": (
                "希望聊天回复支持 Markdown 与代码高亮，用本地依赖不走 CDN。\n"
                "仓库：git@github.com:cheerly-plan/CheerlyHarness.git"
            ),
        },
        {
            "id": "demo-msg-003",
            "subject": "咨询：这个需求该找谁",
            "body": "想问一下产品排期，没有代码仓库。",
        },
    ]

    print("=== 导入样例邮件 ===")
    for message in sample_emails:
        raw = RawItem.from_email(
            subject=message["subject"], body=message["body"], message_id=message["id"]
        )
        item, created = scheduler.import_raw(raw)
        status = "新建" if created else "去重跳过"
        print(f"  [{status}] {item.kind.value:7} {item.title[:40]:40} → "
              f"{item.repo.name if item.repo else '未映射'}")

    print("\n=== dry-run 执行（不改动任何仓库）===")
    for item in store.list():
        if item.repo is None:
            print(f"  [跳过] {item.title[:40]} — 无本地仓库映射")
            continue
        scheduler.run(item.id, exec_mode="dry-run")

    for _ in range(100):
        await asyncio.sleep(0.05)
        if all(item.is_terminal() for item in store.list()):
            break

    print("\n=== 结果报告 ===")
    for item in store.list():
        print(f"  [{item.status.value:10}] {item.title[:40]:40} — {item.report_path}")
        if item.result and item.result.dry_run:
            print(f"      命令预览: {item.result.command[:100]}…")

    print("\ndemo done. 用 `taskagent serve` 打开界面查看完整报告。")


def main() -> None:
    parser = argparse.ArgumentParser(prog="taskagent")
    parser.add_argument("command", choices=["serve", "scan", "demo"])
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    base = Settings.from_env()
    settings = Settings(
        data_dir=base.data_dir,
        host=args.host or base.host,
        port=args.port or base.port,
        project_roots=base.project_roots,
        codebuddy_command=base.codebuddy_command,
        codebuddy_model=base.codebuddy_model,
        timeout_s=base.timeout_s,
        max_concurrent=base.max_concurrent,
        project_map_file=base.project_map_file,
    )

    if args.command == "serve":
        serve(settings)
    elif args.command == "scan":
        scan(settings)
    else:
        asyncio.run(demo(settings))


if __name__ == "__main__":
    main()
