"""TAPD 回填行为：dry-run 不改状态、评论带改动文件与 diff、副作用互不阻塞。"""

from __future__ import annotations

from taskagent.backfill import (
    COMMENT_LIMIT,
    backfill_result,
    build_comment,
    parse_tapd_ref,
    should_backfill,
)
from taskagent.models import ItemStatus, RunResult, WorkItem


class FakeConfig:
    primary_workspace_id = "ws-1"
    status_on_success = "resolved"
    status_on_failure = ""


class FakeClient:
    def __init__(self, *, fail_comment: bool = False, fail_status: bool = False) -> None:
        self.config = FakeConfig()
        self.comments: list[str] = []
        self.statuses: list[str] = []
        self.targets: list[object] = []
        self.calls: list[str] = []
        self.fail_comment = fail_comment
        self.fail_status = fail_status

    def add_comment(self, item, content: str) -> bool:
        self.calls.append("comment")
        self.targets.append(item)
        if self.fail_comment:
            raise RuntimeError("comment rejected")
        self.comments.append(content)
        return True

    def update_status(self, item, status: str) -> bool:
        self.calls.append("status")
        if self.fail_status:
            raise RuntimeError("status rejected")
        self.statuses.append(status)
        return True


def make_item(
    *,
    dry_run: bool = False,
    ok: bool = True,
    files: tuple[str, ...] = (),
    diff: str = "",
    merge_state: str = "",
) -> WorkItem:
    item = WorkItem.create(
        title="登录失败",
        description="点登录后 500",
        kind="bug",
        source="tapd",
        source_ref="tapd:bug:100",
    )
    item.status = ItemStatus.SUCCEEDED if ok else ItemStatus.FAILED
    item.result = RunResult(
        ok=ok,
        dry_run=dry_run,
        command="codebuddy -p …",
        changed_files=list(files),
        diff=diff,
    )
    item.merge_state = merge_state
    item.report_path = "/tmp/work-1.md"
    return item


def test_parse_tapd_ref() -> None:
    assert parse_tapd_ref("tapd:story:12") == ("story", "12")
    assert parse_tapd_ref("tapd:bug:99") == ("bug", "99")
    assert parse_tapd_ref("manual:abc") is None
    assert parse_tapd_ref("") is None


def test_dry_run_touches_nothing_on_tapd() -> None:
    item = make_item(dry_run=True)
    item.plan = "1. 阅读相关代码\n2. 定位根因"
    client = FakeClient()

    backfill_result(item, True, client=client)

    # dry-run 只生成本地方案：不改状态，也不发评论制造噪音。
    assert should_backfill(item) is False
    assert client.calls == []
    assert client.comments == []
    assert client.statuses == []


def test_apply_comment_lists_files_and_diff_and_writes_status() -> None:
    item = make_item(
        files=("src/a.py", "web/b.tsx"),
        diff="diff --git a/src/a.py b/src/a.py\n+fixed_login",
        merge_state="merged",
    )
    item.tapd_workspace_id = "ws-42"
    client = FakeClient()

    backfill_result(item, True, client=client)

    assert client.calls == ["comment", "status"]  # 评论先落，状态后写
    assert client.statuses == ["resolved"]
    comment = client.comments[0]
    assert "已完成" in comment
    assert "src/a.py" in comment and "web/b.tsx" in comment
    assert "+fixed_login" in comment
    assert comment.index("src/a.py") < comment.index("+fixed_login")
    # 本机路径对 TAPD 上的人没有意义，评论里不再出现。
    assert "本机报告" not in comment
    assert "/tmp/work-1.md" not in comment
    # 多项目时写回必须落到工单所属项目，而不是第一个配置的项目。
    assert client.targets[0].workspace_id == "ws-42"


def test_long_diff_is_truncated_within_budget() -> None:
    diff = "\n".join(f"+line {index:04d}" for index in range(1000))
    item = make_item(files=("src/a.py",), diff=diff, merge_state="merged")

    comment = build_comment(item, ok=True)

    assert len(comment) <= COMMENT_LIMIT
    assert "已截断" in comment
    assert "src/a.py" in comment
    # 文件清单在 diff 之前，截断不会把它切掉。
    assert comment.index("src/a.py") < comment.index("+line")


def test_failed_run_comment_explains_reason_and_reports_status() -> None:
    item = make_item(ok=False, files=("src/a.py",))
    item.error = "验证未通过：pytest 失败"
    item.result.verification = {"ok": False, "state": "failed", "reason": "pytest 失败"}
    client = FakeClient()
    client.config.status_on_failure = "reopened"

    backfill_result(item, False, client=client)

    assert client.statuses == ["reopened"]
    comment = client.comments[0]
    assert "未完成" in comment
    assert "pytest 失败" in comment
    assert "src/a.py" in comment


def test_blocked_merge_comment_says_nothing_landed() -> None:
    item = make_item(files=("src/a.py",), merge_state="blocked")
    item.result.sync_conflicts = ["src/a.py (冲突)"]
    client = FakeClient()
    client.config.status_on_failure = "reopened"

    backfill_result(item, False, client=client)

    assert client.statuses == ["reopened"]
    comment = client.comments[0]
    assert "未合并" in comment
    assert "隔离区" in comment
    assert "1 个文件" in comment


def test_comment_failure_does_not_block_status_write() -> None:
    item = make_item(files=("src/a.py",), merge_state="merged")
    client = FakeClient(fail_comment=True)

    backfill_result(item, True, client=client)  # 不抛异常

    assert client.calls == ["comment", "status"]
    assert client.statuses == ["resolved"]


def test_status_failure_does_not_raise() -> None:
    item = make_item(files=("src/a.py",), merge_state="merged")
    client = FakeClient(fail_status=True)

    backfill_result(item, True, client=client)  # 不抛异常

    assert len(client.comments) == 1
    assert client.statuses == []


def test_non_tapd_item_is_ignored() -> None:
    item = make_item(dry_run=True)
    item.source_ref = "manual:123"
    client = FakeClient()

    backfill_result(item, True, client=client)

    assert client.calls == []

def test_forced_merge_is_called_out_in_comment() -> None:
    item = make_item(files=("src/a.py",), merge_state="merged")
    item.result.verification = {
        "ok": False, "state": "failed", "reason": "pytest 失败",
        "override": True, "override_by": "user",
    }

    comment = build_comment(item, ok=True)

    assert "强制合并" in comment
    assert "跳过门禁" in comment
