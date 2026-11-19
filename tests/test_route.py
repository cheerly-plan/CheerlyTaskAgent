"""多仓库分流：历史学习 → 文本点名 → 角色契合 → 文本相关度 → 待确认。"""

from __future__ import annotations

from taskagent.repoprofile import RepoProfile
from taskagent.route import RouteHistory, route_item


class Candidate:
    def __init__(self, id_, name, aliases=None):
        self.id = id_
        self.name = name
        self.aliases = aliases or []


def _profile(id_, name, *, roles=None, description="", keywords=None) -> RepoProfile:
    return RepoProfile(
        mapping_id=id_, name=name, path=f"/tmp/{name}",
        roles=roles or [], description=description, keywords=keywords or [],
    )


def test_single_binding_is_auto():
    decision = route_item(workspace_id="w", title="任意", candidates=[Candidate("m1", "Only")], profiles={})
    assert decision.repo == "Only"
    assert decision.method == "auto"
    assert not decision.needs_confirm


def test_no_candidates_needs_confirm():
    decision = route_item(workspace_id="w", title="x", candidates=[], profiles={})
    assert decision.repo == ""
    assert decision.needs_confirm


def test_history_wins(tmp_path):
    history = RouteHistory(tmp_path / "h.json")
    history.record("w1", "会员中心页面优化", "Chat")
    decision = route_item(
        workspace_id="w1",
        title="会员中心页面改版",
        candidates=[Candidate("m1", "Chat"), Candidate("m2", "Backend")],
        profiles={},
        history=history,
    )
    assert decision.repo == "Chat"
    assert decision.method == "history"
    assert "历史选择" in decision.reason


def test_history_ignored_for_other_workspace(tmp_path):
    history = RouteHistory(tmp_path / "h.json")
    history.record("w1", "会员中心页面优化", "Chat")
    decision = route_item(
        workspace_id="w2",
        title="会员中心页面改版",
        candidates=[Candidate("m1", "Chat"), Candidate("m2", "Backend")],
        profiles={},
        history=history,
    )
    assert decision.method != "history"


def test_explicit_repo_name_in_text():
    decision = route_item(
        workspace_id="w",
        title="请修改 CheerlyAIChat 的会员页",
        candidates=[Candidate("m1", "CheerlyAIChat"), Candidate("m2", "CheerlyAgent")],
        profiles={},
    )
    assert decision.repo == "CheerlyAIChat"
    assert decision.method == "explicit"


def test_role_matching_prefers_backend():
    profiles = {
        "m1": _profile("m1", "ai_backend", roles=["backend"]),
        "m2": _profile("m2", "ai_chat", roles=["frontend"]),
    }
    decision = route_item(
        workspace_id="w",
        title="新增会员接口与数据库表结构",
        description="需要鉴权和缓存",
        candidates=[Candidate("m1", "ai_backend"), Candidate("m2", "ai_chat")],
        profiles=profiles,
    )
    assert decision.repo == "ai_backend"
    assert decision.method == "role"


def test_role_matching_prefers_frontend():
    profiles = {
        "m1": _profile("m1", "ai_backend", roles=["backend"]),
        "m2": _profile("m2", "ai_chat", roles=["frontend"]),
    }
    decision = route_item(
        workspace_id="w",
        title="会员页面的按钮样式和交互优化",
        candidates=[Candidate("m1", "ai_backend"), Candidate("m2", "ai_chat")],
        profiles=profiles,
    )
    assert decision.repo == "ai_chat"
    assert decision.method == "role"


def test_text_overlap_picks_matching_repo():
    profiles = {
        "m1": _profile("m1", "svg-tool", description="图标批量转换脚本"),
        "m2": _profile("m2", "chat-app", description="聊天与会话服务"),
    }
    decision = route_item(
        workspace_id="w",
        title="聊天会话列表分页",
        candidates=[Candidate("m1", "svg-tool"), Candidate("m2", "chat-app")],
        profiles=profiles,
    )
    assert decision.repo == "chat-app"


def test_ambiguous_when_no_evidence():
    profiles = {
        "m1": _profile("m1", "alpha", roles=["frontend"]),
        "m2": _profile("m2", "beta", roles=["backend"]),
    }
    decision = route_item(
        workspace_id="w",
        title="Zzz",
        candidates=[Candidate("m1", "alpha"), Candidate("m2", "beta")],
        profiles=profiles,
    )
    assert decision.method == "ambiguous"
    assert decision.needs_confirm
    assert len(decision.candidates) == 2


def test_history_record_counts_up(tmp_path):
    history = RouteHistory(tmp_path / "h.json")
    history.record("w", "会员页", "A")
    history.record("w", "会员页", "A")
    repo, score, _ = history.suggest("w", "会员页改版")
    assert repo == "A"
    assert score > 0.75
