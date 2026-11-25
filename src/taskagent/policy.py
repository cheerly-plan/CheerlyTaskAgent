"""从「提示词记忆」升级而来的策略建议：高频失败 -> 收紧仓库白名单。

设计边界（与 memory.py 完全解耦，纯增量）：

- 失败事件与建议落在独立文件 ``data/policy-advisor.json``；不读写、不清洗
  ``memory.json``，旧记忆条目和旧调用方保持原样。
- 生成建议**只写本模块自己的存储**：不改生效白名单、不改权限配置、不参与
  准入判定；生成路径从不读取 ``auto_apply`` 去自动应用。
- 只有显式调用 :meth:`PolicyAdvisor.apply` 才更新 ``automation.repo_allowlist``
  （并写入 ``repo_denylist`` 兜底），随后由现有 ``admission.admit`` 拒绝命中仓库。
- 所有配置（阈值 / 时间窗 / 冷却）缺失或非法时回落到下方文档化默认值并告警，
  绝不因脏配置崩溃或产生建议风暴。
- 失败原文沿用 ``verification`` 的脱敏规则后再写入建议与审计日志。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .memory import failure_signature
from .verification import _redact, _secret_values

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger(__name__)

SUGGESTION_TYPE = "tighten_repo_whitelist"
STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_REVERTED = "reverted"

# 文档化默认值：配置缺失 / 非法时统一回落到这里。
DEFAULT_THRESHOLD = 3
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_COOLDOWN_MINUTES = 1440
DEFAULT_AUTO_APPLY = False

POLICY_UI_DEFAULTS: dict[str, Any] = {
    "threshold": DEFAULT_THRESHOLD,
    "window_minutes": DEFAULT_WINDOW_MINUTES,
    "cooldown_minutes": DEFAULT_COOLDOWN_MINUTES,
    "auto_apply": DEFAULT_AUTO_APPLY,
}

ENV_THRESHOLD = "TASKAGENT_POLICY_THRESHOLD"
ENV_WINDOW_MINUTES = "TASKAGENT_POLICY_WINDOW_MINUTES"
ENV_COOLDOWN_MINUTES = "TASKAGENT_POLICY_COOLDOWN_MINUTES"
ENV_AUTO_APPLY = "TASKAGENT_POLICY_AUTO_APPLY"

_MAX_EVENTS = 2_000
_MAX_SUGGESTIONS = 500


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime:
    """Parse a stored ISO timestamp; unknown/corrupt values sort as very old."""
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _positive_int(value: Any, default: int, name: str) -> int:
    """Coerce a config value to a positive int; warn and fall back otherwise."""
    if isinstance(value, bool):
        logger.warning("策略配置 %s=%r 非法（非数值），回落到默认值 %s", name, value, default)
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        logger.warning("策略配置 %s=%r 非法（非数值），回落到默认值 %s", name, value, default)
        return default
    if number <= 0:
        logger.warning("策略配置 %s=%r 非法（须为正数），回落到默认值 %s", name, value, default)
        return default
    return number


def _bool(value: Any, default: bool, name: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    logger.warning("策略配置 %s=%r 非法（非布尔），回落到默认值 %s", name, value, default)
    return default


@dataclass(frozen=True)
class PolicyConfig:
    """Resolved thresholds. ``auto_apply`` defaults off and is never read by generation."""

    threshold: int = DEFAULT_THRESHOLD
    window_minutes: int = DEFAULT_WINDOW_MINUTES
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES
    auto_apply: bool = DEFAULT_AUTO_APPLY

    @classmethod
    def from_mapping(
        cls,
        value: Any = None,
        *,
        environ: Any = None,
    ) -> PolicyConfig:
        """Merge defaults <- environment <- explicit mapping, validating each field.

        Missing config (``None`` / non-dict / absent keys) and illegal values
        (0, negatives, non-numeric) all fall back to the documented defaults.
        UI settings win over environment variables when both are present.
        """
        data = value if isinstance(value, dict) else {}
        env = os.environ if environ is None else environ
        if env is None or not hasattr(env, "get"):
            env = {}
        raw_window = env.get(ENV_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES)
        raw_cooldown = env.get(ENV_COOLDOWN_MINUTES, DEFAULT_COOLDOWN_MINUTES)
        return cls(
            threshold=_positive_int(
                data.get("threshold", env.get(ENV_THRESHOLD, DEFAULT_THRESHOLD)),
                DEFAULT_THRESHOLD, "threshold",
            ),
            window_minutes=_positive_int(
                data.get("window_minutes", raw_window),
                DEFAULT_WINDOW_MINUTES, "window_minutes",
            ),
            cooldown_minutes=_positive_int(
                data.get("cooldown_minutes", raw_cooldown),
                DEFAULT_COOLDOWN_MINUTES, "cooldown_minutes",
            ),
            auto_apply=_bool(
                data.get("auto_apply", env.get(ENV_AUTO_APPLY, DEFAULT_AUTO_APPLY)),
                DEFAULT_AUTO_APPLY, "auto_apply",
            ),
        )

    @classmethod
    def from_env(cls) -> PolicyConfig:
        return cls.from_mapping({})

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "window_minutes": self.window_minutes,
            "cooldown_minutes": self.cooldown_minutes,
            "auto_apply": self.auto_apply,
        }


@dataclass
class FailureEvent:
    """One countable failure, deduplicated by task id within a cluster."""

    id: str
    task_id: str
    repo: str
    cluster_key: str
    failure_type: str
    created_at: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "repo": self.repo,
            "cluster_key": self.cluster_key,
            "failure_type": self.failure_type,
            "created_at": self.created_at,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: Any) -> FailureEvent | None:
        if not isinstance(data, dict) or not data.get("cluster_key"):
            return None
        return cls(
            id=str(data.get("id") or ""),
            task_id=str(data.get("task_id") or ""),
            repo=str(data.get("repo") or ""),
            cluster_key=str(data.get("cluster_key") or ""),
            failure_type=str(data.get("failure_type") or ""),
            created_at=str(data.get("created_at") or ""),
            evidence=str(data.get("evidence") or ""),
        )


@dataclass
class PolicySuggestion:
    """An auditable proposal. Applying it requires an explicit human call."""

    id: str
    type: str
    cluster_key: str
    repo: str
    hits: int
    threshold: int
    window_minutes: int
    evidence: list[str] = field(default_factory=list)
    # 脱敏后的证据摘要内联在建议里：事件被上限裁剪后仍可追溯，不依赖事件仍在存储中。
    evidence_samples: list[str] = field(default_factory=list)
    sample_evidence: str = ""
    created_at: str = field(default_factory=lambda: _iso(_now()))
    status: str = STATUS_PENDING
    applied_at: str = ""
    applied_allowlist: list[str] = field(default_factory=list)
    # 应用前白名单快照，仅供审计；撤销走增量，不用它整体覆盖。
    prior_allowlist: list[str] = field(default_factory=list)
    # 本条 apply 是否真的往 denylist 新增了目标；撤销只回退自己写入的 deny，
    # 不解除运维手工加入的拒绝。旧数据缺字段时按 True 处理（保守回退）。
    applied_deny_added: bool = True
    applied_source: str = ""
    applied_actor: str = ""
    reverted_at: str = ""
    reverted_source: str = ""
    reverted_actor: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "cluster_key": self.cluster_key,
            "repo": self.repo,
            "hits": self.hits,
            "threshold": self.threshold,
            "window_minutes": self.window_minutes,
            "evidence": list(self.evidence),
            "evidence_samples": list(self.evidence_samples),
            "sample_evidence": self.sample_evidence,
            "created_at": self.created_at,
            "status": self.status,
            "applied_at": self.applied_at,
            "applied_allowlist": list(self.applied_allowlist),
            "prior_allowlist": list(self.prior_allowlist),
            "applied_deny_added": self.applied_deny_added,
            "applied_source": self.applied_source,
            "applied_actor": self.applied_actor,
            "reverted_at": self.reverted_at,
            "reverted_source": self.reverted_source,
            "reverted_actor": self.reverted_actor,
        }

    @classmethod
    def from_dict(cls, data: Any) -> PolicySuggestion | None:
        if not isinstance(data, dict) or not data.get("cluster_key"):
            return None

        def as_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError, OverflowError):
                return default

        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or SUGGESTION_TYPE),
            cluster_key=str(data.get("cluster_key") or ""),
            repo=str(data.get("repo") or ""),
            hits=as_int(data.get("hits"), 0),
            threshold=as_int(data.get("threshold"), DEFAULT_THRESHOLD),
            window_minutes=as_int(data.get("window_minutes"), DEFAULT_WINDOW_MINUTES),
            evidence=[str(item) for item in (data.get("evidence") or [])],
            evidence_samples=[
                str(item) for item in (data.get("evidence_samples") or [])
            ],
            sample_evidence=str(data.get("sample_evidence") or ""),
            created_at=str(data.get("created_at") or _iso(_now())),
            status=str(data.get("status") or STATUS_PENDING),
            applied_at=str(data.get("applied_at") or ""),
            applied_allowlist=[str(item) for item in (data.get("applied_allowlist") or [])],
            prior_allowlist=[str(item) for item in (data.get("prior_allowlist") or [])],
            applied_deny_added=bool(data.get("applied_deny_added", True)),
            applied_source=str(data.get("applied_source") or ""),
            applied_actor=str(data.get("applied_actor") or ""),
            reverted_at=str(data.get("reverted_at") or ""),
            reverted_source=str(data.get("reverted_source") or ""),
            reverted_actor=str(data.get("reverted_actor") or ""),
        )


def _countable_failure(item: Any) -> bool:
    """Mirror memory.record's failure filter so environment noise never counts."""
    result = getattr(item, "result", None)
    if result is None or getattr(result, "dry_run", False):
        return False
    if str(getattr(result, "failure_scope", "") or "") not in {"", "repo"}:
        return False
    status = getattr(getattr(item, "status", ""), "value", getattr(item, "status", ""))
    if status not in {"succeeded", "failed"}:
        return False
    gate = getattr(result, "verification", {})
    gate = gate if isinstance(gate, dict) else {}
    return bool(
        status != "succeeded"
        or not gate.get("ok", True)
        or str(getattr(item, "merge_state", "") or "") == "blocked"
    )


def cluster_key_of(item: Any) -> tuple[str, str]:
    """Stable key: repository + failure signature (checks/categories = tool names)."""
    repo = getattr(getattr(item, "repo", None), "name", "") or ""
    signature, _summary = failure_signature(item)
    return f"{repo}|{signature}", signature


class PolicyAdvisor:
    """Incremental, auditable policy-suggestion store with an explicit apply gate."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._events, self._suggestions = self._load()

    # ── persistence ───────────────────────────────────────────────────────────
    def _load(self) -> tuple[list[FailureEvent], list[PolicySuggestion]]:
        if not self.path.is_file():
            return [], []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("策略建议存储损坏，忽略后重建：%s", self.path)
            return [], []
        if not isinstance(data, dict):
            return [], []
        raw_events = data.get("events") or []
        raw_suggestions = data.get("suggestions") or []
        events = [
            FailureEvent.from_dict(row)
            for row in raw_events
            if isinstance(row, dict)
        ]
        suggestions = [
            PolicySuggestion.from_dict(row)
            for row in raw_suggestions
            if isinstance(row, dict)
        ]
        return (
            [event for event in events if event is not None][-_MAX_EVENTS:],
            [item for item in suggestions if item is not None][-_MAX_SUGGESTIONS:],
        )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "events": [event.to_dict() for event in self._events],
            "suggestions": [item.to_dict() for item in self._suggestions],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── config ────────────────────────────────────────────────────────────────
    def config(self, settings: Settings | None = None) -> PolicyConfig:
        """Resolve policy config from saved UI settings (env is the base layer)."""
        if settings is None:
            return PolicyConfig.from_env()
        saved = settings.load_ui_settings().get("policy")
        return PolicyConfig.from_mapping(saved)

    # ── read ──────────────────────────────────────────────────────────────────
    def suggestions(self, repo: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [
                item.to_dict()
                for item in self._suggestions
                if not repo or item.repo == repo
            ]

    def events(self, repo: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [
                event.to_dict()
                for event in self._events
                if not repo or event.repo == repo
            ]

    def get(self, suggestion_id: str) -> PolicySuggestion | None:
        with self._lock:
            return next(
                (item for item in self._suggestions if item.id == suggestion_id), None
            )

    # ── failure collection + generation ──────────────────────────────────────
    def observe(
        self,
        item: Any,
        *,
        settings: Settings | None = None,
        now: datetime | None = None,
    ) -> PolicySuggestion | None:
        """Record one countable failure and maybe emit a single suggestion.

        Never touches the effective whitelist, permission config or admission
        decision — it only writes this module's own store and an audit log.
        """
        if not _countable_failure(item):
            return None
        cluster_key, signature = cluster_key_of(item)
        if not signature or "|" not in cluster_key:
            return None
        repo = cluster_key.split("|", 1)[0]
        if not repo:
            return None
        moment = now or _now()
        config = self.config(settings)
        start = moment - timedelta(minutes=config.window_minutes)
        task_id = str(getattr(item, "id", "") or "")
        attempt = getattr(item, "attempt", 0)
        result = getattr(item, "result", None)
        raw = (
            getattr(item, "error", "")
            or getattr(result, "error", "")
            or getattr(item, "stage", "")
            or ""
        )
        # 同一个 task 的重试/崩溃重排是不同次真实失败：去重键带上 attempt；
        # 且只在窗口内去重，窗口外同一 task 可以再次计数。
        dedup_id = f"{task_id}#{attempt}" if task_id else ""
        event = FailureEvent(
            id=uuid.uuid4().hex[:16],
            task_id=dedup_id or uuid.uuid4().hex[:16],
            repo=repo,
            cluster_key=cluster_key,
            failure_type=signature,
            created_at=_iso(moment),
            evidence=_clip(_redact(str(raw), _secret_values()), 200),
        )
        with self._lock:
            if any(
                stored.task_id == dedup_id
                and stored.cluster_key == cluster_key
                and start <= _parse_time(stored.created_at) <= moment
                for stored in self._events
            ):
                return None
            self._events.append(event)
            if len(self._events) > _MAX_EVENTS:
                self._events = self._events[-_MAX_EVENTS:]
            self._save()
        return self._maybe_suggest(cluster_key, repo, config, moment)

    def _maybe_suggest(
        self,
        cluster_key: str,
        repo: str,
        config: PolicyConfig,
        moment: datetime,
    ) -> PolicySuggestion | None:
        start = moment - timedelta(minutes=config.window_minutes)
        with self._lock:
            # 窗口需有上下界：时钟回拨或补录的"未来"事件不得计入。
            hits = [
                event for event in self._events
                if event.cluster_key == cluster_key
                and start <= _parse_time(event.created_at) <= moment
            ]
            if len(hits) < config.threshold:
                return None
            if self._in_cooldown(cluster_key, config, moment):
                return None
            suggestion = PolicySuggestion(
                id="pol-" + uuid.uuid4().hex[:12],
                type=SUGGESTION_TYPE,
                cluster_key=cluster_key,
                repo=repo,
                hits=len(hits),
                threshold=config.threshold,
                window_minutes=config.window_minutes,
                evidence=[event.id for event in hits],
                evidence_samples=[event.evidence for event in hits],
                sample_evidence=hits[-1].evidence,
                created_at=_iso(moment),
            )
            self._suggestions.append(suggestion)
            if len(self._suggestions) > _MAX_SUGGESTIONS:
                self._suggestions = self._suggestions[-_MAX_SUGGESTIONS:]
            self._save()
        # 审计日志：稳定聚类键 + 命中次数必须可检索；详情整条可读。
        logger.warning(
            "策略建议审计：type=%s cluster_key=%s repo=%s hits=%d threshold=%d window_minutes=%d",
            SUGGESTION_TYPE, cluster_key, repo, len(hits),
            config.threshold, config.window_minutes,
        )
        logger.info("策略建议详情：%s", json.dumps(suggestion.to_dict(), ensure_ascii=False))
        return suggestion

    def _in_cooldown(
        self,
        cluster_key: str,
        config: PolicyConfig,
        moment: datetime,
    ) -> bool:
        for suggestion in self._suggestions:
            if suggestion.cluster_key != cluster_key:
                continue
            # applied 长期抑制：收紧仍在生效，重复生成没有意义。
            if suggestion.status == STATUS_APPLIED:
                return True
            # reverted 只按冷却窗口抑制：误确认撤销后若该仓库继续高频失败，
            # 冷却到期应能重新给建议，而不是让该聚类永久失效。
            if moment - _parse_time(suggestion.created_at) < timedelta(
                minutes=config.cooldown_minutes
            ):
                return True
        return False

    # ── explicit human confirmation / apply / revert ─────────────────────────
    def apply(
        self,
        suggestion_id: str,
        *,
        allowed_repos: list[str] | None = None,
        settings: Settings | None = None,
        source: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        """Explicitly tighten the whitelist for one suggestion.

        This is the *only* path that mutates the effective allowlist. The new
        allowlist is derived solely from the current config source by removing
        the target repo; a client-supplied ``allowed_repos`` is never used as
        the source of the effective list, so this path cannot widen access. The
        target is also added to ``repo_denylist`` so enforcement rejects it
        even when the allowlist is empty (meaning "allow all").

        A non-empty allowlist is never emptied by removing its only entry: an
        empty list means "allow all", so doing that would widen access to every
        other repo while only denying the target. In that case the list is kept
        and ``repo_denylist`` alone carries the restriction.
        """
        with self._lock:
            suggestion = next(
                (item for item in self._suggestions if item.id == suggestion_id), None
            )
            if suggestion is None:
                return {"ok": False, "error": "建议不存在", "id": suggestion_id}
            if suggestion.status == STATUS_APPLIED:
                return {
                    "ok": True, "already_applied": True,
                    "suggestion": suggestion.to_dict(),
                }
            target = suggestion.repo
            if settings is None:
                from .config import Settings  # local import: config imports our defaults

                settings = Settings.from_env()
            automation = dict(settings.load_ui_settings().get("automation") or {})
            current = [str(item) for item in (automation.get("repo_allowlist") or [])]
            if allowed_repos:
                extra = [item for item in allowed_repos if item not in current]
                if extra:
                    logger.warning("策略建议应用忽略非配置来源的仓库名单：%s", extra)
            # 收紧绝不新增仓库。但「非空白名单」不能被移除目标后清空：空名单
            # 的语义是「放行全部」，此时 denylist 只挡住目标一个，其余仓库反而
            # 从「拒绝」变为「允许」，是一次权限放宽。保留原名单，由 denylist
            # 精确拒绝目标，其余仓库仍因不在白名单而被拒。
            deduped = list(dict.fromkeys(current))
            new_allow = [repo for repo in deduped if repo != target]
            if current and not new_allow:
                new_allow = deduped
            deny = [str(item) for item in (automation.get("repo_denylist") or [])]
            deny_added = target not in deny
            if deny_added:
                deny.append(target)
            automation["repo_allowlist"] = new_allow[:200]
            automation["repo_denylist"] = list(dict.fromkeys(deny))[:200]
            # 先记录状态字段再写配置；若状态持久化失败，回滚配置与状态，
            # 避免「白名单已收紧但建议仍是 pending、无法 revert」的悬空态。
            old_fields = (
                suggestion.status, suggestion.applied_at,
                list(suggestion.applied_allowlist), list(suggestion.prior_allowlist),
                suggestion.applied_deny_added,
                suggestion.applied_source, suggestion.applied_actor,
            )
            suggestion.status = STATUS_APPLIED
            suggestion.applied_at = _iso(_now())
            suggestion.applied_allowlist = list(automation["repo_allowlist"])
            suggestion.prior_allowlist = list(current)
            suggestion.applied_deny_added = deny_added
            suggestion.applied_source = source or "manual"
            suggestion.applied_actor = actor or "operator"
            saved = settings.save_ui_settings({"automation": automation})
            try:
                self._save()
            except Exception:
                try:
                    settings.save_ui_settings({"automation": {
                        **automation,
                        "repo_allowlist": current,
                        "repo_denylist": [
                            str(item) for item in (automation.get("repo_denylist") or [])
                            if not (deny_added and item == target)
                        ],
                    }})
                except Exception as rollback_error:  # noqa: BLE001
                    logger.error(
                        "策略建议应用回滚配置失败：id=%s error=%s",
                        suggestion_id, rollback_error,
                    )
                (
                    suggestion.status, suggestion.applied_at,
                    suggestion.applied_allowlist, suggestion.prior_allowlist,
                    suggestion.applied_deny_added,
                    suggestion.applied_source, suggestion.applied_actor,
                ) = old_fields
                raise
            result = suggestion.to_dict()
        logger.info(
            "策略建议应用审计：id=%s cluster_key=%s repo=%s hits=%d source=%s actor=%s 新白名单=%s",
            suggestion_id, result["cluster_key"], target, result["hits"],
            result["applied_source"], result["applied_actor"], result["applied_allowlist"],
        )
        return {
            "ok": True,
            "suggestion": result,
            "automation": dict(saved.get("automation") or {}),
        }

    def revert(
        self,
        suggestion_id: str,
        *,
        settings: Settings | None = None,
        source: str = "",
        actor: str = "",
    ) -> dict[str, Any]:
        """Undo a manual confirmation by *incrementally* reversing this apply.

        Gives an operator a way back after a mistaken confirmation. Like
        :meth:`apply`, this is an explicit call only — generation never uses it.

        The snapshot in ``prior_allowlist`` is only an audit record, never a
        value to restore wholesale: apply may have been followed by other
        manual tightenings, and reverting one suggestion must not undo them.
        Revert just adds the target back and drops it from the deny list, and
        refuses when another still-applied suggestion targets the same repo.
        """
        with self._lock:
            suggestion = next(
                (item for item in self._suggestions if item.id == suggestion_id), None
            )
            if suggestion is None:
                return {"ok": False, "error": "建议不存在", "id": suggestion_id}
            if suggestion.status == STATUS_REVERTED:
                return {
                    "ok": True, "already_reverted": True,
                    "suggestion": suggestion.to_dict(),
                }
            if suggestion.status != STATUS_APPLIED:
                return {
                    "ok": False, "error": "建议尚未应用，无需撤销", "id": suggestion_id,
                }
            target = suggestion.repo
            # 同一仓库可能有多个聚类键的建议；只要还有别的建议处于 applied，
            # 撤销本条就会放开仍被其他收紧覆盖的仓库，必须拒绝。
            if any(
                other.id != suggestion_id
                and other.repo == target
                and other.status == STATUS_APPLIED
                for other in self._suggestions
            ):
                return {
                    "ok": False,
                    "error": f"仓库 {target} 仍有其他已应用建议，拒绝撤销以免放宽访问",
                    "id": suggestion_id,
                }
            if settings is None:
                from .config import Settings  # local import: config imports our defaults

                settings = Settings.from_env()
            automation = dict(settings.load_ui_settings().get("automation") or {})
            # 回滚快照必须在任何修改之前取，否则存的是新值、回滚无效。
            old_automation = dict(automation)
            # 只回退本条 apply 写入的 deny；运维手工加入的拒绝保持不动。
            if suggestion.applied_deny_added:
                deny = [str(item) for item in (automation.get("repo_denylist") or [])]
                automation["repo_denylist"] = [item for item in deny if item != target][:200]
            # 增量恢复：仅当本条 apply 确实把 target 移出了白名单时才加回，
            # 且不覆盖 apply 之后运维对名单做的其他改动。
            allow = [
                str(item) for item in (automation.get("repo_allowlist") or [])
            ]
            prior = suggestion.prior_allowlist
            applied_allow = suggestion.applied_allowlist
            # 空名单表示「放行全部」，此时目标已被放行，加回反而会误收紧；
            # 仅在名单非空且本条 apply 确实移出过目标时增量加回。
            if allow and target in prior and target not in applied_allow and target not in allow:
                allow.append(target)
            automation["repo_allowlist"] = list(dict.fromkeys(allow))[:200]
            old_fields = (
                suggestion.status, suggestion.reverted_at,
                suggestion.reverted_source, suggestion.reverted_actor,
            )
            saved = settings.save_ui_settings({"automation": automation})
            suggestion.status = STATUS_REVERTED
            suggestion.reverted_at = _iso(_now())
            suggestion.reverted_source = source or "manual"
            suggestion.reverted_actor = actor or "operator"
            try:
                self._save()
            except Exception:
                try:
                    settings.save_ui_settings({"automation": old_automation})
                except Exception as rollback_error:  # noqa: BLE001
                    logger.error(
                        "策略建议撤销回滚配置失败：id=%s error=%s",
                        suggestion_id, rollback_error,
                    )
                (
                    suggestion.status, suggestion.reverted_at,
                    suggestion.reverted_source, suggestion.reverted_actor,
                ) = old_fields
                raise
            result = suggestion.to_dict()
        logger.info(
            "策略建议撤销审计：id=%s cluster_key=%s repo=%s source=%s actor=%s",
            suggestion_id, result["cluster_key"], target,
            result["reverted_source"], result["reverted_actor"],
        )
        return {
            "ok": True,
            "suggestion": result,
            "automation": dict(saved.get("automation") or {}),
        }
