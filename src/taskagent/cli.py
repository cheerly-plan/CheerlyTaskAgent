"""CLI: serve the UI, scan repos, import emails, or run an offline demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from .backends import build_registry
from .config import Settings
from .events import EventBus
from .gitmap import GitProjectRegistry
from .plans import PlanError, PlanStore
from .projectmap import ProjectMappingStore, ProjectRegistry
from .reviewer import llm_from_settings as review_llm
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
    registry = build_registry(settings)
    executor = registry.resolve(registry.default_backend_id)
    scheduler = Scheduler(
        store=store,
        gitmap=gitmap,
        executors=registry,
        events=events,
        settings=SchedulerSettings(
            max_concurrent=settings.max_concurrent, reports_dir=settings.reports_dir,
            gc_enabled=settings.gc_enabled,
            backup_retention_days=settings.backup_retention_days,
            backup_max_per_task=settings.backup_max_per_task,
            review_provider=lambda: review_llm(settings),
            plan_store=PlanStore(settings.plans_dir),
            plan_policy=lambda: settings.load_ui_settings().get("plan_layer", {}),
        ),
        projects=projects,
    )
    return store, gitmap, projects, events, executor, scheduler


def serve(settings: Settings) -> None:
    import uvicorn

    print(f"TaskAgent → http://{settings.host}:{settings.port}")
    uvicorn.run(
        "taskagent.api:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        # Last-resort cap: even if a connection/task misbehaves during
        # shutdown, force-exit after this many seconds instead of hanging.
        timeout_graceful_shutdown=5,
    )


def build_web() -> None:
    """构建前端（web/ → src/taskagent/web/）。产物不入仓，首次运行前需要执行一次。"""
    import shutil
    import subprocess

    web_src = Path(__file__).resolve().parents[2] / "web"
    if not (web_src / "package.json").is_file():
        raise SystemExit(f"未找到前端工程：{web_src}")
    pnpm = shutil.which("pnpm") or shutil.which("npm")
    if not pnpm:
        raise SystemExit("未找到 pnpm / npm，请先安装 Node.js 18+ 后再构建前端。")
    for command in ([pnpm, "install"], [pnpm, "run", "build"]):
        print("$", " ".join(command), f"(cwd={web_src})")
        result = subprocess.run(command, cwd=web_src, check=False)
        if result.returncode != 0:
            raise SystemExit(f"构建失败（退出码 {result.returncode}）")
    print("\n前端已构建到 src/taskagent/web/，重启 `taskagent serve` 后刷新页面即可。")


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
        try:
            scheduler.run(item.id, exec_mode="dry-run")
        except PlanError as error:
            # 方案门禁拒绝：跳过这一条，demo 继续跑其余任务。
            print(f"  [跳过] {item.title[:40]} — 方案未确认（{error.code}）")

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


# ── spec 断言：可复现命令 ──────────────────────────────────────────────────────
def _read_spec(path: str | None) -> dict[str, Any]:
    """读取 spec JSON；同时接受 ``{"spec": {...}}`` 形式的工作项文件。"""
    raw = sys.stdin.read() if not path or path == "-" else Path(path).expanduser().read_text(
        encoding="utf-8"
    )
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise SystemExit(f"spec 不是合法 JSON：{error}") from error
    if not isinstance(data, dict):
        raise SystemExit("spec 必须是 JSON 对象")
    if "spec" in data and "acceptance" not in data and isinstance(data["spec"], dict):
        return data["spec"]
    return data


def gen_tests(args: argparse.Namespace) -> int:
    """从 spec 生成确定性用例；同一输入两次输出字节级一致。"""
    from .assertions import compile_spec, render_cases

    compilation = compile_spec(_read_spec(args.spec), spec_id=args.spec_id or "")
    text = render_cases(compilation)
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def spec_gate(args: argparse.Namespace) -> int:
    """只按 spec 用例结果判定，输出机器可读 JSON 报告并返回约定退出码。"""
    from .assertions import gate_exit_code, run_assertion_gate

    config: dict[str, Any] = {"enabled": True}
    if args.assertions_config:
        try:
            loaded = json.loads(
                Path(args.assertions_config).expanduser().read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise SystemExit(f"断言配置读取失败：{error}") from error
        if isinstance(loaded, dict):
            config.update(loaded)
    if args.unverified_policy:
        config["unverified_policy"] = args.unverified_policy
    if args.timeout is not None:
        config["timeout_s"] = args.timeout

    report = run_assertion_gate(
        _read_spec(args.spec),
        worktree=args.worktree or ".",
        assertions=config,
        spec_id=args.spec_id or "",
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return gate_exit_code(report)


_GEN_TESTS_COMMANDS = ("gen-tests", "spec-cases", "spec-tests", "compile-spec")
_SPEC_GATE_COMMANDS = ("spec-gate", "assert-gate")


def main() -> int:
    parser = argparse.ArgumentParser(prog="taskagent")
    parser.add_argument(
        "command",
        choices=["serve", "scan", "demo", "web-build",
                 *_GEN_TESTS_COMMANDS, *_SPEC_GATE_COMMANDS],
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--spec", default=None, help="spec JSON 文件；'-' 表示 stdin")
    parser.add_argument("--spec-id", default=None)
    parser.add_argument("--out", default=None, help="报告/用例输出路径（默认 stdout）")
    parser.add_argument("--worktree", default=None, help="用例执行的隔离目录")
    parser.add_argument("--assertions-config", default=None, help="断言门禁配置 JSON")
    parser.add_argument("--unverified-policy", default=None)
    parser.add_argument("--timeout", type=float, default=None, help="单用例超时秒数")
    args = parser.parse_args()

    if args.command in _GEN_TESTS_COMMANDS:
        return gen_tests(args)
    if args.command in _SPEC_GATE_COMMANDS:
        return spec_gate(args)

    base = Settings.from_env()
    # Replace only the CLI-overridable fields. Hand-assembling Settings here
    # silently dropped fs_root / auto_sync / gc_* / available_models etc. for
    # scan and demo; dataclasses.replace keeps every current and future field.
    settings = replace(base, host=args.host or base.host, port=args.port or base.port)

    if args.command == "web-build":
        build_web()
    elif args.command == "serve":
        serve(settings)
    elif args.command == "scan":
        scan(settings)
    else:
        asyncio.run(demo(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
