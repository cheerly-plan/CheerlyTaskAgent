"""TAPD 结果回填：状态写回 + 结果评论。

只有**真正执行过**（apply）的 run 才回写 TAPD：

- dry-run 只生成本地方案、没改代码，既不写状态也不发评论——给来源工单留
  一条"我没改代码"的评论只是噪音，同一条工单还可能被反复 dry-run；
- apply 结束后先发结果评论（结论/改动文件/diff 节选），再按结果写状态。

评论里不写本机报告路径：TAPD 上的人打不开本机文件，写了只是噪音；要看细节，
评论里的 diff 就是改动内容，完整报告留在调度台。
"""

from __future__ import annotations

import logging

from .models import ItemKind, WorkItem
from .tapd import TapdItem

logger = logging.getLogger(__name__)

# tapd.add_comment() 会把 description 截到 2000 字符，这里留余量并自己控制
# 截断位置，保证不会把 diff 的中间某行切成半截。
COMMENT_LIMIT = 1900
MAX_TITLE = 120
MAX_REASON = 300
MAX_STAGE = 200
MAX_FILE_LINES = 15
MIN_DIFF_CHARS = 120

_KIND_LABEL = {
    ItemKind.BUG: "缺陷",
    ItemKind.FEATURE: "需求",
    ItemKind.TASK: "任务",
}


def parse_tapd_ref(source_ref: str) -> tuple[str, str] | None:
    """'tapd:story:123' -> ('story', '123'); anything else -> None.

    拆解出的子任务 source_ref 形如 ``tapd:story:123#sub-1``：它们共享父工单
    的上下文，但不是 TAPD 上真实存在的工单，绝不能拿去回填（会打到不存在的
    ID）。父工单的结果由 :func:`backfill_parent_result` 在子任务全部结束后
    汇总回填。
    """
    parts = (source_ref or "").split(":")
    if len(parts) == 3 and parts[0] == "tapd" and parts[1] in {"story", "bug", "task"}:
        if "#" in parts[2] or not parts[2].strip():
            return None
        return parts[1], parts[2]
    return None


def should_backfill(item: WorkItem) -> bool:
    """Whether this run should touch the source system at all.

    dry-run never touches code, so it must neither close the ticket nor post a
    comment: the ticket owner would see "TaskAgent commented" for work that
    did not happen, and one ticket can be dry-run many times.
    """
    result = item.result
    return not (result is not None and result.dry_run)


def _one_line(text: str, limit: int) -> str:
    """Collapse whitespace so a multi-line title can't break the layout."""
    return " ".join((text or "").split())[:limit]


def _verification_failure(item: WorkItem) -> str:
    result = item.result
    verification = result.verification if result is not None else {}
    if isinstance(verification, dict) and verification and not verification.get("ok", True):
        return "验证未通过：" + str(verification.get("reason") or "未知原因")
    return ""


def _headline(item: WorkItem, ok: bool) -> str:
    result = item.result
    if item.merge_state == "blocked":
        return "未合并：改动仍在隔离区"
    if not ok:
        return "未完成"
    verification = result.verification if result is not None else {}
    if isinstance(verification, dict) and verification.get("override"):
        return "已强制合并（跳过验证）"
    return "已完成"


def _reason(item: WorkItem, ok: bool) -> str:
    """Why the run did not deliver (empty when it did)."""
    if ok and item.merge_state != "blocked":
        return ""
    verification = _verification_failure(item)
    if verification:
        return verification
    if item.error:
        return item.error
    result = item.result
    if item.merge_state == "blocked":
        conflicts = len(result.sync_conflicts) if result is not None else 0
        if conflicts:
            return f"自动合并冲突（{conflicts} 个文件），一个文件都没有写入主仓库"
        return "自动合并失败，没有写入主仓库"
    return item.stage or "未完成"


def build_comment(item: WorkItem, *, ok: bool, budget: int = COMMENT_LIMIT) -> str:
    """Compose the TAPD comment for one finished execution."""
    result = item.result
    files = list(result.changed_files) if result is not None else []

    lines = [f"【TaskAgent】{_headline(item, ok)}"]
    lines.append(f"任务：{_one_line(item.title, MAX_TITLE)}（{_KIND_LABEL.get(item.kind, '任务')}）")
    if item.source_ref:
        lines.append(f"来源：{item.source_ref}")
    lines.append(f"执行后端：{item.executor}；模型：{item.model or '后端默认'}")

    reason = _reason(item, ok)
    if reason:
        lines.append(f"原因：{_one_line(reason, MAX_REASON)}")
    verification = result.verification if result is not None else {}
    if isinstance(verification, dict) and verification.get("override"):
        lines.append("注意：本单验证未通过，已由人工强制合并（跳过门禁）。")
    if item.stage:
        lines.append(f"状态：{_one_line(item.stage, MAX_STAGE)}")
    if result is not None and int(result.rounds or 1) > 1:
        lines.append(f"执行轮次：{int(result.rounds)} 轮（含自动修复）")
    review = verification.get("review") if isinstance(verification, dict) else None
    if isinstance(review, dict) and review.get("state") == "done":
        verdict = {"approve": "通过", "comment": "有建议", "block": "阻塞"}.get(
            str(review.get("verdict") or ""), str(review.get("verdict") or "")
        )
        issues = len(review.get("issues") or [])
        lines.append(f"评审：{verdict}（{issues} 个问题）")
    delivery = item.delivery if isinstance(item.delivery, dict) else {}
    if delivery.get("pr_url"):
        lines.append(f"交付：PR/MR {delivery['pr_url']}")
    elif delivery.get("commit"):
        pushed = "，已推送" if delivery.get("pushed") else ""
        lines.append(
            f"交付：分支 {delivery.get('branch') or '—'} @ {delivery['commit']}{pushed}"
        )
    if delivery.get("error"):
        lines.append(f"交付失败：{_one_line(str(delivery['error']), 200)}")
    usage = item.usage if isinstance(item.usage, dict) else {}
    usage_total = int(usage.get("total_tokens") or 0)
    if usage_total:
        cli = int((usage.get("cli") or {}).get("total_tokens") or 0)
        llm = int((usage.get("llm") or {}).get("total_tokens") or 0)
        lines.append(f"Token：{usage_total}（执行 {cli} / Agent {llm}）")

    if files:
        lines.append(f"改动文件（{len(files)}）：")
        for name in files[:MAX_FILE_LINES]:
            lines.append(f"- {name}")
        if len(files) > MAX_FILE_LINES:
            lines.append(f"- …另有 {len(files) - MAX_FILE_LINES} 个")
    else:
        lines.append("改动文件：无")

    # Diff goes last so truncation never cuts the file list or the reason.
    diff = (result.diff if result is not None else "") or ""
    if diff:
        head_len = len("\n".join(lines))
        prefix = "\n改动内容（diff 节选）：\n"
        room = budget - head_len - len(prefix) - 20
        if room >= MIN_DIFF_CHARS:
            excerpt = diff[:room]
            lines.append("改动内容（diff 节选）：")
            lines.append(excerpt.rstrip())
            if len(excerpt) < len(diff):
                lines.append(f"…（diff 共 {len(diff)} 字符，已截断）")
        else:
            lines.append("改动内容：diff 较长，已省略")

    text = "\n".join(lines)
    if len(text) > budget:
        text = text[: budget - 20].rstrip() + "\n…（评论已截断）"
    return text


def build_children_comment(
    parent: WorkItem, children: list[WorkItem], *, ok: bool
) -> str:
    """Aggregated comment for a decomposed parent (one comment, not N)."""
    done = sum(
        1 for child in children
        if child.status.value == "succeeded" and child.merge_state != "blocked"
    )
    lines = [
        f"【TaskAgent】拆解执行{'完成' if ok else '有失败'}：{done}/{len(children)} 个子任务成功",
        f"父需求：{_one_line(parent.title, MAX_TITLE)}",
    ]
    for child in children:
        mark = "✅" if child.status.value == "succeeded" and child.merge_state != "blocked" else "❌"
        line = f"{mark} {_one_line(child.title, 80)}（{child.status.value}）"
        if child.status.value != "succeeded" and child.error:
            line += f"：{_one_line(child.error, 120)}"
        lines.append(line)
        if len("\n".join(lines)) > COMMENT_LIMIT - 100:
            lines.append("…（其余子任务略）")
            break
    return "\n".join(lines)[:COMMENT_LIMIT]


def backfill_parent_result(
    parent: WorkItem, children: list[WorkItem], *, ok: bool, client
) -> None:
    """Write the aggregated outcome of a decomposed parent back to TAPD."""
    parsed = parse_tapd_ref(parent.source_ref)
    if not parsed:
        return
    entity_type, object_id = parsed
    workspace_id = (
        str(getattr(parent, "tapd_workspace_id", "") or "").strip()
        or client.config.primary_workspace_id
    )
    target = TapdItem(
        entity_type=entity_type,
        id=object_id,
        workspace_id=workspace_id,
        title="", description="", status="", owner="", priority="",
        iteration_id="", created="", modified="",
    )
    try:
        client.add_comment(target, build_children_comment(parent, children, ok=ok))
    except Exception as error:  # noqa: BLE001 - best effort, same as single-task path
        logger.warning("父需求 %s 的 TAPD 评论回填失败：%s", parent.id, error)
    status = client.config.status_on_success if ok else client.config.status_on_failure
    if not status:
        return
    try:
        client.update_status(target, status)
    except Exception as error:  # noqa: BLE001
        logger.warning("父需求 %s 的 TAPD 状态回填失败：%s", parent.id, error)


def backfill_result(item: WorkItem, ok: bool, *, client) -> None:
    """Post the comment and (unless dry-run) the status transition.

    ``client`` is a :class:`taskagent.tapd.TapdClient` (or a test double).
    Each side effect is isolated: a rejected comment never prevents the status
    write, and vice versa.
    """
    if not should_backfill(item):
        return
    parsed = parse_tapd_ref(item.source_ref)
    if not parsed:
        return
    entity_type, object_id = parsed
    # 多项目配置下 primary_workspace_id 只是第一个；写回必须落到工单所属项目，
    # 否则状态/评论会打到别的项目里（或被 TAPD 拒绝）。
    workspace_id = (
        str(getattr(item, "tapd_workspace_id", "") or "").strip()
        or client.config.primary_workspace_id
    )
    target = TapdItem(
        entity_type=entity_type,
        id=object_id,
        workspace_id=workspace_id,
        title="", description="", status="", owner="", priority="",
        iteration_id="", created="", modified="",
    )
    try:
        client.add_comment(target, build_comment(item, ok=ok))
    except Exception as error:  # noqa: BLE001 - never mask the local result
        logger.warning("工作项 %s 的 TAPD 评论回填失败：%s", item.id, error)

    status = client.config.status_on_success if ok else client.config.status_on_failure
    if not status:
        return
    try:
        client.update_status(target, status)
    except Exception as error:  # noqa: BLE001 - never mask the local result
        logger.warning("工作项 %s 的 TAPD 状态回填失败：%s", item.id, error)
