"""记忆升级为策略：阈值聚类、审计、冷却去重、人工确认应用。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from taskagent.admission import AutomationPolicy, admit
from taskagent.api import create_app
from taskagent.config import Settings
from taskagent.memory import TaskMemory, memory_text
from taskagent.models import ItemStatus, LocalRepo, RunResult, WorkItem
from taskagent.policy import (
    DEFAULT_AUTO_APPLY,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_THRESHOLD,
    DEFAULT_WINDOW_MINUTES,
    ENV_THRESHOLD,
    ENV_WINDOW_MINUTES,
    POLICY_UI_DEFAULTS,
    SUGGESTION_TYPE,
    PolicyAdvisor,
    PolicyConfig,
)


class FakeSettings:
    """Minimal settings double: policy config + in-memory automation config."""

    def __init__(self, *, policy: dict[str, Any] | None = None,
                 automation: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = {
            "policy": dict(policy or {}),
            "automation": dict(automation or {}),
        }

    def load_ui_settings(self) -> dict[str, Any]:
        return self.data

    def save_ui_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key, value in payload.items():
            self.data[key] = value
        return dict(self.data)


def _failure(tmp_path: Path, *, repo: str = "repo-a", tool: str = "lint") -> WorkItem:
    item = WorkItem.create(title="失败任务", description="x", kind="bug", source="tapd")
    item.repo = LocalRepo(path=str(tmp_path / repo), origin="", name=repo)
    item.status = ItemStatus.FAILED
    item.result = RunResult(
        ok=False,
        dry_run=False,
        command="x",
        verification={
            "ok": False,
            "state": "failed",
            "checks": [{"id": tool, "category": tool, "ok": False}],
        },
        rounds=1,
    )
    item.error = "验证未通过：" + tool
    return item


def _admit_item(tmp_path: Path, repo: str) -> WorkItem:
    item = WorkItem.create(title="准入探测", description="x", kind="bug", source="tapd")
    item.repo = LocalRepo(path=str(tmp_path / repo), origin="", name=repo)
    item.status = ItemStatus.READY
    return item


NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ── 验收 4：配置默认值、覆盖与非法回落 ────────────────────────────────────────
def test_policy_config_defaults_and_invalid_fallback(caplog):
    cfg = PolicyConfig.from_mapping({}, environ={})
    assert cfg.threshold == DEFAULT_THRESHOLD
    assert cfg.window_minutes == DEFAULT_WINDOW_MINUTES
    assert cfg.cooldown_minutes == DEFAULT_COOLDOWN_MINUTES
    assert cfg.auto_apply is DEFAULT_AUTO_APPLY
    assert cfg.auto_apply is False

    with caplog.at_level(logging.WARNING, logger="taskagent.policy"):
        bad = PolicyConfig.from_mapping(
            {"threshold": 0, "window_minutes": -5, "cooldown_minutes": "abc"},
            environ={},
        )
    assert bad.threshold == DEFAULT_THRESHOLD
    assert bad.window_minutes == DEFAULT_WINDOW_MINUTES
    assert bad.cooldown_minutes == DEFAULT_COOLDOWN_MINUTES
    assert sum("策略配置" in record.message for record in caplog.records) >= 3

    # 环境变量是底层，UI 设置覆盖之。
    assert PolicyConfig.from_mapping({}, environ={ENV_THRESHOLD: "7"}).threshold == 7
    assert PolicyConfig.from_mapping({"threshold": 2}, environ={ENV_THRESHOLD: "7"}).threshold == 2
    # 非法 UI 值不应把环境变量的合法值带崩，整体回落到文档默认值。
    assert PolicyConfig.from_mapping(
        {"threshold": None}, environ={ENV_THRESHOLD: "7"}
    ).threshold == DEFAULT_THRESHOLD
    assert POLICY_UI_DEFAULTS["auto_apply"] is False


# ── 验收 1：阈值前无建议、字段完整、只生成一条 ────────────────────────────────
def test_below_threshold_no_suggestion(tmp_path: Path):
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 3})
    assert advisor.observe(_failure(tmp_path), settings=settings, now=NOW) is None
    assert advisor.observe(_failure(tmp_path), settings=settings, now=NOW) is None
    assert advisor.suggestions() == []


def test_threshold_generates_one_complete_suggestion(tmp_path: Path, caplog):
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 3, "window_minutes": 60})
    with caplog.at_level(logging.WARNING, logger="taskagent.policy"):
        suggestion = None
        for _ in range(3):
            suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    row = suggestion.to_dict()
    assert row["type"] == SUGGESTION_TYPE == "tighten_repo_whitelist"
    assert row["repo"] == "repo-a"
    assert "repo-a" in row["cluster_key"] and "lint" in row["cluster_key"]
    assert row["hits"] == 3
    assert row["threshold"] == 3
    assert row["window_minutes"] == 60
    assert len(row["evidence"]) == 3
    assert isinstance(row["created_at"], str) and row["created_at"]
    assert len(advisor.suggestions()) == 1
    assert any(
        "策略建议审计" in record.message and "hits=3" in record.message
        and "repo-a" in record.message
        for record in caplog.records
    )


def test_same_cluster_in_cooldown_does_not_regenerate(tmp_path: Path):
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 2, "cooldown_minutes": 1440})
    first = None
    for _ in range(2):
        first = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert first is not None
    for _ in range(4):
        assert advisor.observe(_failure(tmp_path), settings=settings, now=NOW) is None
    assert len(advisor.suggestions()) == 1

    # 冷却结束后旧事件已滑出时间窗，也不会凭空补一条建议。
    later = NOW + timedelta(minutes=1500)
    assert advisor.observe(_failure(tmp_path), settings=settings, now=later) is None
    # 换仓库是另一个聚类键，独立计数、独立建议。
    other = None
    for _ in range(2):
        other = advisor.observe(_failure(tmp_path, repo="repo-b"), settings=settings, now=NOW)
    assert other is not None and other.repo == "repo-b"
    assert len(advisor.suggestions()) == 2


def test_time_window_excludes_stale_events(tmp_path: Path):
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 2, "window_minutes": 10})
    stale = NOW - timedelta(minutes=30)
    # 陈旧事件先记录；用倒退的 now 再 observe 会把"未来"事件算进窗口，
    # 所以按时间正序推进：陈旧事件应落在当前 10 分钟窗口之外。
    assert advisor.observe(_failure(tmp_path), settings=settings, now=stale) is None
    assert advisor.observe(_failure(tmp_path), settings=settings, now=NOW) is None
    hit = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert hit is not None and hit.hits == 2


def test_repeated_same_task_not_double_counted(tmp_path: Path):
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 2})
    item = _failure(tmp_path)
    assert advisor.observe(item, settings=settings, now=NOW) is None
    assert advisor.observe(item, settings=settings, now=NOW) is None
    assert advisor.suggestions() == []


# ── 验收 3：生成不改白名单/准入；只有显式 apply 才收紧 ────────────────────────
def test_generation_keeps_whitelist_and_admission_unchanged(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({"automation": {"repo_allowlist": ["repo-a", "repo-b"]}})
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings.save_ui_settings({"policy": {"threshold": 1}})
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None

    automation = settings.load_ui_settings()["automation"]
    assert automation["repo_allowlist"] == ["repo-a", "repo-b"]
    assert automation.get("repo_denylist", []) == []
    policy = AutomationPolicy.from_settings(automation)
    assert admit(_admit_item(tmp_path, "repo-a"), policy).ok
    assert admit(_admit_item(tmp_path, "repo-b"), policy).ok


def test_apply_then_enforcement_denies_only_target(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({"automation": {"repo_allowlist": ["repo-a", "repo-b"]}})
    settings.save_ui_settings({"policy": {"threshold": 1}})
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None

    result = advisor.apply(
        suggestion.id, allowed_repos=["repo-a", "repo-b"], settings=settings
    )
    assert result["ok"] is True
    assert result["suggestion"]["status"] == "applied"
    automation = settings.load_ui_settings()["automation"]
    assert "repo-a" not in automation["repo_allowlist"]
    assert "repo-b" in automation["repo_allowlist"]

    policy = AutomationPolicy.from_settings(automation)
    denied = admit(_admit_item(tmp_path, "repo-a"), policy)
    allowed = admit(_admit_item(tmp_path, "repo-b"), policy)
    assert not denied.ok and denied.code == "repo_not_allowed"
    assert allowed.ok
    # 幂等：重复应用不会改写为未知状态。
    assert advisor.apply(suggestion.id, settings=settings)["already_applied"] is True


def test_apply_with_empty_allowlist_still_denies_target(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({"policy": {"threshold": 1}})
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    advisor.apply(suggestion.id, allowed_repos=[], settings=settings)
    policy = AutomationPolicy.from_settings(settings.load_ui_settings()["automation"])
    assert not admit(_admit_item(tmp_path, "repo-a"), policy).ok


def test_apply_single_repo_allowlist_does_not_widen_access(tmp_path: Path):
    """收紧唯一允许的仓库时，空名单的「放行全部」语义不能反转权限。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None

    advisor.apply(suggestion.id, settings=settings)
    automation = settings.load_ui_settings()["automation"]
    # 名单不能被删空：空列表意味着放行全部仓库。
    assert automation["repo_allowlist"] == ["repo-a"]
    assert automation["repo_denylist"] == ["repo-a"]

    policy = AutomationPolicy.from_settings(automation)
    assert not admit(_admit_item(tmp_path, "repo-a"), policy).ok
    assert not admit(_admit_item(tmp_path, "repo-b"), policy).ok
    assert not admit(_admit_item(tmp_path, "repo-c"), policy).ok


# ── 验收 5：未触发阈值时提示词文本逐字不变 ────────────────────────────────────
def test_prompt_text_unchanged_without_suggestion(tmp_path: Path):
    memory = TaskMemory(tmp_path / "memory.json")
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    item = _failure(tmp_path)
    memory.record(item)
    before = memory_text(memory.recall(item))
    assert before
    settings = FakeSettings(policy={"threshold": 3})
    for _ in range(2):
        assert advisor.observe(_failure(tmp_path), settings=settings, now=NOW) is None
    after = memory_text(memory.recall(item))
    assert after == before
    assert advisor.suggestions() == []


def test_dry_run_and_environment_failure_are_not_counted(tmp_path: Path):
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 1})
    dry = _failure(tmp_path)
    dry.result.dry_run = True
    assert advisor.observe(dry, settings=settings, now=NOW) is None
    env = _failure(tmp_path)
    env.result.failure_scope = "environment"
    assert advisor.observe(env, settings=settings, now=NOW) is None
    assert advisor.suggestions() == []


# ── 验收 2：完整建议可查询 + API 人工确认 ────────────────────────────────────
def test_api_query_and_apply(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path, project_roots=()))
    with TestClient(app) as client:
        assert client.get("/api/policy/suggestions").json()["suggestions"] == []
        client.put("/api/settings", json={
            "policy": {"threshold": 2},
            "automation": {"repo_allowlist": ["repo-a", "repo-b"]},
        })
        advisor = app.state.ctx.policy
        for _ in range(2):
            advisor.observe(_failure(tmp_path), settings=app.state.ctx.settings, now=NOW)

        listed = client.get("/api/policy/suggestions").json()
        assert len(listed["suggestions"]) == 1
        row = listed["suggestions"][0]
        assert row["type"] == SUGGESTION_TYPE
        assert row["hits"] == 2 and len(row["evidence"]) == 2
        assert listed["config"]["threshold"] == 2

        missing = client.post("/api/policy/suggestions/does-not-exist/apply")
        assert missing.status_code == 404

        applied = client.post(
            "/api/policy/suggestions/" + row["id"] + "/apply",
            json={"allowed_repos": ["repo-a", "repo-b"]},
        )
        assert applied.status_code == 200
        assert applied.json()["suggestion"]["status"] == "applied"
        assert client.get("/api/settings").json()["automation"]["repo_allowlist"] == ["repo-b"]


# ── 评审补强：真实配置源、不可放宽、运行期准入、撤销、证据内联 ──────────────────
def test_real_settings_env_override_and_invalid_fallback(
    tmp_path: Path, monkeypatch, caplog
):
    """验收 4：真实 Settings 下环境变量生效，UI 值逐字段覆盖，非法值回落。"""
    monkeypatch.setenv(ENV_THRESHOLD, "4")
    monkeypatch.setenv(ENV_WINDOW_MINUTES, "15")
    settings = Settings(data_dir=tmp_path)
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    cfg = advisor.config(settings)
    assert cfg.threshold == 4
    assert cfg.window_minutes == 15
    assert cfg.cooldown_minutes == DEFAULT_COOLDOWN_MINUTES

    # UI 只保存 threshold：其余字段继续取环境变量播种值，而不是被 UI 默认值遮蔽。
    settings.save_ui_settings({"policy": {"threshold": 2}})
    cfg = advisor.config(settings)
    assert cfg.threshold == 2
    assert cfg.window_minutes == 15

    with caplog.at_level(logging.WARNING, logger="taskagent.policy"):
        settings.save_ui_settings({"policy": {"threshold": 0}})
        cfg = advisor.config(settings)
    assert cfg.threshold == DEFAULT_THRESHOLD
    assert any("策略配置" in record.message for record in caplog.records)


def test_api_apply_cannot_widen_allowlist(tmp_path: Path, caplog):
    """安全：请求体传入的仓库名单不能新增生效白名单中的仓库。"""
    settings = Settings(data_dir=tmp_path, project_roots=())
    settings.save_ui_settings({"automation": {"repo_allowlist": ["repo-a", "repo-b"]}})
    app = create_app(settings)
    with TestClient(app) as client:
        client.put("/api/settings", json={"policy": {"threshold": 1}})
        advisor = app.state.ctx.policy
        suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
        assert suggestion is not None

        with caplog.at_level(logging.WARNING, logger="taskagent.policy"):
            applied = client.post(
                "/api/policy/suggestions/" + suggestion.id + "/apply",
                json={"allowed_repos": ["repo-a", "repo-b", "evil-repo"]},
            )
        assert applied.status_code == 200
        allowlist = client.get("/api/settings").json()["automation"]["repo_allowlist"]
        assert allowlist == ["repo-b"]
        assert "evil-repo" not in allowlist
        assert any("忽略非配置来源" in record.message for record in caplog.records)
        assert applied.json()["suggestion"]["applied_source"] == "api"
        assert applied.json()["suggestion"]["applied_actor"] == "operator"
        assert applied.json()["suggestion"]["prior_allowlist"] == ["repo-a", "repo-b"]


def test_api_apply_runtime_admission_e2e(tmp_path: Path):
    """验收 3：API 确认后，运行期准入策略（dispatch 所用）拒绝目标仓库。"""
    settings = Settings(data_dir=tmp_path, project_roots=())
    settings.save_ui_settings({"automation": {"repo_allowlist": ["repo-a", "repo-b"]}})
    app = create_app(settings)
    with TestClient(app) as client:
        client.put("/api/settings", json={"policy": {"threshold": 1}})
        advisor = app.state.ctx.policy
        suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
        assert suggestion is not None

        runtime = app.state.ctx.automation._policy()
        assert admit(_admit_item(tmp_path, "repo-a"), runtime).ok
        assert admit(_admit_item(tmp_path, "repo-b"), runtime).ok

        applied = client.post(
            "/api/policy/suggestions/" + suggestion.id + "/apply", json={}
        )
        assert applied.status_code == 200
        runtime = app.state.ctx.automation._policy()
        denied = admit(_admit_item(tmp_path, "repo-a"), runtime)
        allowed = admit(_admit_item(tmp_path, "repo-b"), runtime)
        assert not denied.ok and denied.code == "repo_not_allowed"
        assert allowed.ok


def test_api_revert_restores_whitelist(tmp_path: Path):
    """人工误确认可回滚：撤销后目标仓库重新被运行期准入放行。"""
    settings = Settings(data_dir=tmp_path, project_roots=())
    settings.save_ui_settings({"automation": {"repo_allowlist": ["repo-a", "repo-b"]}})
    app = create_app(settings)
    with TestClient(app) as client:
        client.put("/api/settings", json={"policy": {"threshold": 1}})
        advisor = app.state.ctx.policy
        suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
        assert suggestion is not None
        sid = suggestion.id
        assert client.post(
            "/api/policy/suggestions/" + sid + "/apply", json={}
        ).status_code == 200
        assert not admit(
            _admit_item(tmp_path, "repo-a"), app.state.ctx.automation._policy()
        ).ok

        reverted = client.post(
            "/api/policy/suggestions/" + sid + "/revert", json={"actor": "tester"}
        )
        assert reverted.status_code == 200
        assert reverted.json()["suggestion"]["status"] == "reverted"
        assert reverted.json()["suggestion"]["reverted_actor"] == "tester"
        assert admit(
            _admit_item(tmp_path, "repo-a"), app.state.ctx.automation._policy()
        ).ok
        again = client.post("/api/policy/suggestions/" + sid + "/revert", json={})
        assert again.json()["already_reverted"] is True


def test_suggestion_inlines_evidence_samples(tmp_path: Path):
    """审计证据内联：事件被上限裁剪后建议仍可追溯，旧数据缺字段也可读。"""
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 2})
    suggestion = None
    for _ in range(2):
        suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    row = suggestion.to_dict()
    assert len(row["evidence_samples"]) == len(row["evidence"]) == 2
    assert all(isinstance(item, str) and item for item in row["evidence_samples"])

    reloaded = PolicyAdvisor(tmp_path / "policy.json").suggestions()[0]
    assert len(reloaded["evidence_samples"]) == 2


def test_revert_is_incremental_and_preserves_later_tightening(tmp_path: Path):
    """撤销只回退本条改动，不得用旧快照抹掉 apply 之后的人工收紧。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a", "repo-b", "repo-c"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    advisor.apply(suggestion.id, settings=settings)
    # apply 之后运维又删掉 repo-c（进一步收紧）。
    automation = settings.load_ui_settings()["automation"]
    automation["repo_allowlist"] = [r for r in automation["repo_allowlist"] if r != "repo-c"]
    settings.save_ui_settings({"automation": automation})

    advisor.revert(suggestion.id, settings=settings)
    final = settings.load_ui_settings()["automation"]
    assert "repo-a" in final["repo_allowlist"]  # 本条目标恢复
    assert "repo-c" not in final["repo_allowlist"]  # 后续收紧不被抹掉
    assert "repo-a" not in final["repo_denylist"]


def test_revert_refused_when_other_applied_suggestion_targets_same_repo(tmp_path: Path):
    """同仓多聚类建议：仍有一条 applied 时拒绝撤销，防止放宽。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a", "repo-b"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    s1 = advisor.observe(_failure(tmp_path, tool="lint"), settings=settings, now=NOW)
    s2 = advisor.observe(_failure(tmp_path, tool="test"), settings=settings, now=NOW)
    assert s1 is not None and s2 is not None and s1.repo == s2.repo == "repo-a"
    advisor.apply(s1.id, settings=settings)
    advisor.apply(s2.id, settings=settings)

    result = advisor.revert(s1.id, settings=settings)
    assert result["ok"] is False
    assert "其他已应用建议" in result["error"]
    policy = AutomationPolicy.from_settings(settings.load_ui_settings()["automation"])
    assert not admit(_admit_item(tmp_path, "repo-a"), policy).ok


def test_reverted_suggestion_can_regenerate_after_cooldown(tmp_path: Path):
    """误确认撤销后，冷却到期应能重新生成建议，而非永久抑制该聚类。"""
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 1, "cooldown_minutes": 10})
    s = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert s is not None
    advisor.apply(s.id, settings=settings)
    advisor.revert(s.id, settings=settings)
    # 同一聚类（同 repo + 同签名）的新任务：冷却期内抑制。
    mid = _failure(tmp_path)
    mid.id = "work-mid-cooldown"
    assert advisor.observe(mid, settings=settings, now=NOW) is None
    # 冷却到期后应重新生成。
    later = NOW + timedelta(minutes=11)
    after = _failure(tmp_path)
    after.id = "work-after-cooldown"
    fresh = advisor.observe(after, settings=settings, now=later)
    assert fresh is not None


def test_retry_attempts_count_separately(tmp_path: Path):
    """同一任务的重试（不同 attempt）应作为不同失败计数。"""
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    settings = FakeSettings(policy={"threshold": 2})
    first = _failure(tmp_path)
    first.attempt = 1
    second = _failure(tmp_path)
    second.attempt = 2
    assert advisor.observe(first, settings=settings, now=NOW) is None
    hit = advisor.observe(second, settings=settings, now=NOW)
    assert hit is not None and hit.hits == 2


def test_revert_keeps_manually_added_deny(tmp_path: Path):
    """撤销只回退自身写入的 deny，不解除运维手工加入的拒绝。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a", "repo-b", "repo-c"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    advisor.apply(suggestion.id, settings=settings)
    # 运维手工把 repo-b 也加入 deny。
    automation = settings.load_ui_settings()["automation"]
    automation["repo_denylist"] = sorted(set(automation["repo_denylist"]) | {"repo-b"})
    settings.save_ui_settings({"automation": automation})

    advisor.revert(suggestion.id, settings=settings)
    final = settings.load_ui_settings()["automation"]
    assert "repo-a" not in final["repo_denylist"]  # 本条写入被回退
    assert "repo-b" in final["repo_denylist"]  # 人工设置保留


def test_apply_rolls_back_config_when_state_persist_fails(tmp_path: Path, monkeypatch):
    """状态持久化失败时回滚配置，不留「已收紧但无法撤销」的悬空态。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a", "repo-b"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None

    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(PolicyAdvisor, "_save", boom)
    import pytest
    with pytest.raises(OSError):
        advisor.apply(suggestion.id, settings=settings)

    automation = settings.load_ui_settings()["automation"]
    assert automation["repo_allowlist"] == ["repo-a", "repo-b"]
    assert automation["repo_denylist"] == []
    assert advisor.get(suggestion.id).status == "pending"


def test_revert_persist_failure_restores_original_config(tmp_path: Path, monkeypatch):
    """撤销时状态持久化失败必须回滚到撤销前的配置，而非新值。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a", "repo-b"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    advisor.apply(suggestion.id, settings=settings)
    applied = settings.load_ui_settings()["automation"]

    def boom(self):
        raise OSError("disk full")

    monkeypatch.setattr(PolicyAdvisor, "_save", boom)
    import pytest
    with pytest.raises(OSError):
        advisor.revert(suggestion.id, settings=settings)

    assert settings.load_ui_settings()["automation"] == applied
    assert advisor.get(suggestion.id).status == "applied"


def test_revert_with_now_empty_allowlist_does_not_tighten(tmp_path: Path):
    """apply 后名单被运维清空（放行全部）时，撤销不得把目标加回而误收紧。"""
    settings = Settings(data_dir=tmp_path)
    settings.save_ui_settings({
        "automation": {"repo_allowlist": ["repo-a", "repo-b"]},
        "policy": {"threshold": 1},
    })
    advisor = PolicyAdvisor(tmp_path / "policy.json")
    suggestion = advisor.observe(_failure(tmp_path), settings=settings, now=NOW)
    assert suggestion is not None
    advisor.apply(suggestion.id, settings=settings)
    # 运维清空名单（=放行全部）。
    automation = settings.load_ui_settings()["automation"]
    automation["repo_allowlist"] = []
    settings.save_ui_settings({"automation": automation})

    advisor.revert(suggestion.id, settings=settings)
    final = settings.load_ui_settings()["automation"]
    assert final["repo_allowlist"] == []
    policy = AutomationPolicy.from_settings(final)
    assert admit(_admit_item(tmp_path, "repo-a"), policy).ok
