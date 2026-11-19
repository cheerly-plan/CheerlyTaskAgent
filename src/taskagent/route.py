"""Repo routing for tasks whose TAPD project is bound to several local repos.

Deterministic, explainable decisions only — no LLM here. The layers, in order:

1. **历史学习** — 同一 TAPD 项目 + 同一业务词（模块/标签/标题关键词）历史上
   人工选过哪个仓库；命中即复用，最可靠。
2. **显式标记** — 工单文本里直接出现了仓库名 / 别名（人工已经说清楚）。
3. **角色契合** — 结构化画像的依赖指纹（前端/后端/移动端）与工单文本
   里出现的端别词（页面、接口、小程序…）匹配。
4. **文本相关度** — 画像里的包名 / 描述 / README / 目录 / 提交词与工单文本的重合度。
5. 都不明确 → ``needs_confirm``，交给人选；选完记一笔历史，下次命中第 1 层。

Every decision carries a ``reason`` so the UI and the report can explain it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .repoprofile import RepoProfile

# 工单文本里的「端别」提示词 → 角色。命中越多越可信。
_ROLE_HINTS: dict[str, tuple[str, ...]] = {
    "backend": (
        "接口", "api", "后端", "服务端", "数据库", "表结构", "迁移", "sql",
        "定时任务", "队列", "缓存", "鉴权", "登录态", "网关", "微服务",
    ),
    "frontend": (
        "页面", "前端", "组件", "样式", "交互", "按钮", "弹窗", "布局",
        "路由", "表单", "列表页", "详情页", "ui", "css",
    ),
    "mobile": (
        "小程序", "app", "移动端", "安卓", "android", "ios", "客户端",
        "原生", "rn",
    ),
    "desktop": ("桌面", "桌面端", "electron", "客户端安装包"),
}

_TOKEN_SPLIT = re.compile(r"[\s:：,，。、;；\-_/()（）\[\]【】「」\"'`]+")

# 停用词：太泛，命中也没信息量
_STOPWORDS = {
    "the", "and", "for", "with", "add", "update", "fix", "task", "story", "bug",
    "需求", "缺陷", "任务", "优化", "调整", "修改", "新增", "支持", "功能",
    "问题", "处理", "完善", "实现", "提供", "一个", "可以", "需要", "进行",
}


@dataclass
class RouteDecision:
    """One routing outcome for a work item."""

    repo: str = ""
    # auto | history | explicit | role | text | ambiguous | none
    method: str = "none"
    # 0..1，越高越可信
    confidence: float = 0.0
    reason: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def needs_confirm(self) -> bool:
        return not self.repo or self.confidence < 0.6

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "candidates": self.candidates,
            "needs_confirm": self.needs_confirm,
        }


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN_SPLIT.split((text or "").lower()):
        token = raw.strip()
        if len(token) < 2 or token in _STOPWORDS or token.isdigit():
            continue
        out.append(token)
    return out


def _history_keys(text: str) -> list[str]:
    """历史索引键：英文按词，中文按二字（中文没有空格，整句当词会永远匹配不上）。"""
    keys: list[str] = []
    for token in _tokens(text):
        if token.isascii():
            if len(token) <= 16:
                keys.append(token)
        else:
            # 中文整段只保留有辨识度的二字片，避免整句变键
            keys.extend(b for b in _bigrams(token) if len(b) == 2)
    # 去重但保持顺序，最多 24 个，避免历史文件膨胀
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out[:24]


def _bigrams(text: str) -> set[str]:
    """中文没有空格，用二字滑窗补一点匹配能力（只用于文本相关度）。"""
    clean = re.sub(r"[^\u4e00-\u9fa5a-z0-9]", "", (text or "").lower())
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


@dataclass
class RouteHistory:
    """Remembers which repo was chosen for which (workspace, keyword) pair."""

    path: Path
    # key: f"{workspace_id}|{token}" → {"repo": name, "count": n, "updated_at": t}
    _entries: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        return {str(k): v for k, v in entries.items() if isinstance(v, dict)}

    def save(self) -> None:
        payload = {"entries": self._entries}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, workspace_id: str, text: str, repo: str) -> int:
        """Remember ``repo`` for this workspace + the text's business tokens.

        Also mirrors the whole-workspace choice (``token="*"``) so a project with
        only one sensible repo learns fast.
        """
        from .models import _now

        repo = (repo or "").strip()
        if not repo:
            return 0
        keys = {f"{workspace_id}|*"}
        for token in _history_keys(text):
            keys.add(f"{workspace_id}|{token}")
        for key in keys:
            entry = self._entries.setdefault(key, {"repo": repo, "count": 0, "updated_at": ""})
            if entry.get("repo") == repo:
                entry["count"] = int(entry.get("count", 0)) + 1
            else:
                # 换了选择：以新选择为准，但保留一次计数作为弱证据
                entry.update({"repo": repo, "count": 1})
            entry["updated_at"] = _now()
        self.save()
        return len(keys)

    def suggest(self, workspace_id: str, text: str) -> tuple[str, float, str]:
        """Best historical repo for this workspace + text. Returns (repo, score, reason)."""
        tokens = _history_keys(text)
        best: tuple[str, float, str] = ("", 0.0, "")
        for token in tokens:
            entry = self._entries.get(f"{workspace_id}|{token}")
            if not entry:
                continue
            count = int(entry.get("count", 0))
            # 具体词比「整个项目」可信；次数越多越可信，封顶 0.95
            weight = 0.75 if token != "*" else 0.55
            score = min(0.95, weight + min(count, 5) * 0.04)
            if score > best[1]:
                best = (str(entry.get("repo", "")), score, f"历史选择：{token} 类需求此前用 {entry.get('repo')}（{count} 次）")
        return best


def _role_scores(text: str) -> dict[str, int]:
    lowered = (text or "").lower()
    scores: dict[str, int] = {}
    for role, hints in _ROLE_HINTS.items():
        hit = sum(1 for hint in hints if hint in lowered)
        if hit:
            scores[role] = hit
    return scores


def _text_overlap(profile: RepoProfile, tokens: set[str], bigrams: set[str]) -> float:
    haystack = profile.search_text()
    if not haystack:
        return 0.0
    hay_tokens = set(_tokens(haystack))
    token_hits = len(tokens & hay_tokens)
    bigram_hits = len(bigrams & _bigrams(haystack))
    # 英文词命中权重最高；中文二字命中也有意义（「聊天」「会话」足以说明相关）
    return min(1.0, token_hits * 0.3 + bigram_hits * 0.18)


def route_item(
    *,
    workspace_id: str,
    title: str,
    description: str = "",
    candidates: list[Any],
    profiles: dict[str, RepoProfile],
    history: RouteHistory | None = None,
) -> RouteDecision:
    """Decide which of ``candidates`` (ProjectMapping) should execute this item."""
    if not candidates:
        return RouteDecision(reason="该 TAPD 项目还没有绑定本地仓库")

    names = [c.name for c in candidates]
    if len(candidates) == 1:
        return RouteDecision(
            repo=names[0], method="auto", confidence=1.0, reason="该项目只绑定了一个本地仓库"
        )

    text = f"{title} {description}"
    lowered = text.lower()

    # ── 1) 历史学习 ──────────────────────────────────────────────────────
    if history is not None:
        repo, score, reason = history.suggest(workspace_id, text)
        if repo and repo in names:
            return RouteDecision(repo=repo, method="history", confidence=score, reason=reason)

    # ── 2) 工单文本直接点名仓库 ─────────────────────────────────────────
    for candidate in candidates:
        for token in [candidate.name, *getattr(candidate, "aliases", [])]:
            token = (token or "").strip().lower()
            if token and len(token) >= 3 and token in lowered:
                return RouteDecision(
                    repo=candidate.name,
                    method="explicit",
                    confidence=0.9,
                    reason=f"工单文本直接提到仓库「{candidate.name}」",
                )

    # ── 3) 角色契合（依赖指纹 vs 文本端别词）───────────────────────────
    role_hits = _role_scores(text)
    role_scored: list[tuple[float, str, str]] = []
    for candidate in candidates:
        profile = profiles.get(candidate.id)
        if profile is None:
            continue
        roles = profile.effective_roles()
        match = max((role_hits.get(r, 0) for r in roles), default=0)
        if match:
            role = next(r for r in roles if role_hits.get(r, 0) == match)
            role_scored.append((float(match), candidate.name, f"需求涉及「{role}」，{candidate.name} 是 {role} 工程"))
    role_scored.sort(reverse=True)
    if role_scored:
        top_score, top_name, top_reason = role_scored[0]
        runner_up = role_scored[1][0] if len(role_scored) > 1 else 0
        confidence = 0.85 if top_score >= 2 and top_score > runner_up else 0.65 if top_score > runner_up else 0.5
        return RouteDecision(
            repo=top_name,
            method="role",
            confidence=confidence,
            reason=top_reason,
            candidates=[{"repo": n, "score": s, "reason": r} for s, n, r in role_scored],
        )

    # ── 4) 文本相关度 ───────────────────────────────────────────────────
    tokens = set(_tokens(text))
    grams = _bigrams(text)
    text_scored: list[tuple[float, str, str]] = []
    for candidate in candidates:
        profile = profiles.get(candidate.id)
        if profile is None:
            continue
        score = _text_overlap(profile, tokens, grams)
        if score > 0:
            text_scored.append((score, candidate.name, f"描述/目录/提交记录与需求关键词相关（{score:.2f}）"))
    text_scored.sort(reverse=True)
    if text_scored:
        top_score, top_name, top_reason = text_scored[0]
        runner_up = text_scored[1][0] if len(text_scored) > 1 else 0
        # 只有一个明显领先才敢自动选
        if top_score >= 0.3 and top_score >= runner_up * 1.5:
            return RouteDecision(
                repo=top_name,
                method="text",
                confidence=min(0.8, top_score),
                reason=top_reason,
                candidates=[{"repo": n, "score": round(s, 3), "reason": r} for s, n, r in text_scored],
            )

    # ── 5) 无法判断：交给人选 ───────────────────────────────────────────
    return RouteDecision(
        method="ambiguous",
        confidence=0.0,
        reason=f"该项目绑定 {len(candidates)} 个仓库，没有足够依据自动选择，需人工确认",
        candidates=[
            {
                "repo": c.name,
                "score": round(
                    next((s for s, n, _ in text_scored if n == c.name), 0.0), 3
                ),
                "reason": (profiles.get(c.id).description[:60] if profiles.get(c.id) else ""),
            }
            for c in candidates
        ],
    )