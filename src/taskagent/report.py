"""Local Markdown reports — the only result sink in this phase."""

from __future__ import annotations

from pathlib import Path

from .executor import build_prompt
from .models import WorkItem


def render_report(item: WorkItem) -> str:
    repo = item.repo
    result = item.result
    lines = [
        f"# 工作项报告：{item.title}",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| ID | `{item.id}` |",
        f"| 类型 | {item.kind.value} |",
        f"| 来源 | {item.source.value} {item.source_ref} |",
        f"| 状态 | {item.status.value} |",
        f"| 执行模式 | {item.exec_mode} |",
        f"| git 地址 | {item.git_url or '—'} |",
        f"| 映射仓库 | {repo.path if repo else '未映射'} |",
        f"| 尝试次数 | {item.attempt} |",
        "",
        "## 需求描述",
        "",
        item.description or "—",
        "",
        "## 下发给 codebuddy 的提示词",
        "",
        "```text",
        build_prompt(item).rstrip(),
        "```",
        "",
    ]
    if result:
        lines += [
            "## 执行结果",
            "",
            "| 字段 | 值 |",
            "|---|---|",
            f"| 成功 | {'是' if result.ok else '否'} |",
            f"| dry-run | {'是' if result.dry_run else '否'} |",
            f"| 模型 | {item.model or '执行器默认'} |",
            f"| 耗时 | {result.duration_s:.2f}s |",
            f"| worktree | {result.worktree or '—'} |",
            f"| 错误 | {result.error or '—'} |",
            "",
            "### 命令",
            "",
            "```sh",
            result.command,
            "```",
            "",
        ]
        if result.changed_files:
            lines += ["### 改动文件", ""] + [f"- `{name}`" for name in result.changed_files] + [""]
        # Auto-sync outcome: which files landed in the main repo, what was
        # mirrored as a deletion, and where originals were backed up.
        if result.synced_files or result.sync_deleted or result.sync_error:
            lines += [
                "### 自动同步到主仓库",
                "",
                "| 字段 | 值 |",
                "|---|---|",
                f"| 已同步 | {len(result.synced_files)} 个文件 |",
                f"| 已删除 | {len(result.sync_deleted)} 个文件 |",
                f"| 备份目录 | {result.sync_backup_dir or '—'} |",
                f"| 同步错误 | {result.sync_error or '—'} |",
                "",
            ]
            if result.synced_files:
                lines += ["已写入工作区："] + [f"- `{name}`" for name in result.synced_files] + [""]
            if result.sync_deleted:
                lines += ["已删除："] + [f"- `{name}`" for name in result.sync_deleted] + [""]
        if result.sync_skipped:
            lines += ["同步时跳过（忽略规则/路径不安全/失败）：", ""] + [
                f"- `{name}`" for name in result.sync_skipped
            ] + [""]
        if result.stdout:
            lines += ["### stdout", "", "```text", result.stdout.rstrip(), "```", ""]
        if result.stderr:
            lines += ["### stderr", "", "```text", result.stderr.rstrip(), "```", ""]
    if item.error:
        lines += ["## 错误", "", f"`{item.error}`", ""]
    return "\n".join(lines)


def write_report(item: WorkItem, reports_dir: Path) -> str:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{item.id}.md"
    path.write_text(render_report(item), encoding="utf-8")
    return str(path)
