"""Runtime settings, overridable by environment. No secrets in the repo."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# NOTE: model IDs are no longer hard-coded here. The supported-model list is
# discovered at runtime from the default backend's CLI ``--help`` output (see
# ``Executor.list_models``), so the UI always matches the installed CLI instead
# of a hand-written list that drifts as the CLI upgrades. ``TASKAGENT_MODELS``
# remains an explicit allow-list the operator can set to narrow the choices;
# it wins over discovery when present.

# Settings managed from the UI "系统设置" tab. Stored in <data_dir>/settings.json
# so the values survive restarts; env-derived values are used as the initial
# defaults until the file is written. Backends are configuration-driven: the UI
# may add/remove any number of them, and none is privileged. The default backend
# for new tasks is the ``executor`` key; runtime dispatch falls back to it when a
# task references a missing/disabled backend, and errors only if no backend
# exists at all.
def _backend_profile(
    *,
    label: str = "",
    command: str = "",
    enabled: bool = False,
    model: str = "",
    timeout_s: int = 600,
    auto_sync: bool = True,
) -> dict[str, Any]:
    """A complete, independent profile for one execution backend.

    Used both for the built-in seeds and for backends added at runtime, so a
    newly added backend always carries every field the registry expects.
    """
    return {
        "label": label,
        "enabled": enabled,
        "command": command,
        "model": model,
        "timeout_s": timeout_s,
        "auto_sync": auto_sync,
    }


def _default_backends() -> dict[str, dict[str, Any]]:
    """Initial backend profiles shipped with a fresh install.

    These are only seeds — the persisted ``backends`` map is authoritative and
    may contain any number of extra (or fewer) backends.
    """
    return {
        "codebuddy": _backend_profile(
            label="CodeBuddy CLI", command="codebuddy", enabled=True,
        ),
        "dsh": _backend_profile(label="DSH", command="dsh"),
    }


# Desktop-app bundles ship the CLI at a stable path but rarely put it on PATH.
# Used only when the configured command is a bare name that PATH cannot resolve,
# so an explicit user setting is never overridden.
KNOWN_CLI_PATHS: dict[str, tuple[str, ...]] = {
    "dsh": (
        "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh",
        "~/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh",
    ),
}


def discover_command(command: str) -> str:
    """Return a usable path for ``command`` when the bare name is not on PATH."""
    if not command or "/" in command or (os.altsep and os.altsep in command):
        return command  # explicit path: trust the user
    if shutil.which(command):
        return command  # PATH resolves it; keep the portable bare name
    for candidate in KNOWN_CLI_PATHS.get(command, ()):
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return command


def tapd_to_data_source(tapd: dict[str, Any]) -> dict[str, Any]:
    """Convert the legacy single ``tapd`` settings dict into a ``data_source`` entry."""
    return {
        "type": "tapd",
        "name": "TAPD",
        "enabled": bool(tapd.get("enabled", False)),
        "tapd": {k: v for k, v in tapd.items() if k != "enabled"},
    }


def normalise_data_source(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise the single ``data_source`` object: type/name/enabled + type payload.

    数据源本质上是「一个对象、类型不同」，所以用单一 ``data_source``（而非列表）
    描述。``type`` 决定下方配置表单；目前仅支持 ``tapd``，其他类型留待接入。
    """
    src_type = str(item.get("type") or "tapd").strip() or "tapd"
    name = str(item.get("name") or "").strip() or ("TAPD" if src_type == "tapd" else src_type)
    # type payload: for tapd, nest under ``tapd``; keep unknown types' raw fields.
    tapd = item.get("tapd")
    if not isinstance(tapd, dict):
        tapd = {k: v for k, v in item.items() if k not in {"type", "name", "enabled"}}
    return {
        "type": src_type,
        "name": name,
        "enabled": bool(item.get("enabled", False)),
        "tapd": dict(tapd),
    }


def normalise_model(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise one backend-agent LLM entry from the model configuration page.

    These models drive the backend agent's own OpenAI-compatible calls (e.g.
    planner repository selection); they never configure an execution CLI.
    """
    model_id = str(item.get("id") or "").strip()
    return {
        "id": model_id,
        "label": str(item.get("label") or "").strip() or model_id,
        "provider": str(item.get("provider") or "").strip(),
        "base_url": str(item.get("base_url") or "").strip(),
        "api_key": str(item.get("api_key") or "").strip(),
        "enabled": bool(item.get("enabled", True)),
    }


def _sync_agent_model(data: dict[str, Any]) -> None:
    """Keep ``agent_model`` pointing at a saved entry; blank = first enabled."""
    known = {str(m.get("id")) for m in data.get("models") or [] if isinstance(m, dict)}
    agent_model = str(data.get("agent_model") or "").strip()
    data["agent_model"] = agent_model if agent_model in known else ""


UI_SETTINGS_DEFAULTS: dict[str, Any] = {
    "executor": "codebuddy",  # default backend for new tasks (legacy key, kept for /api/config)
    "codebuddy_command": "codebuddy",
    "dsh_path": "",
    "default_model": "",
    "max_concurrent": 1,
    "auto_sync": True,
    # 后端 Agent 自己使用的 LLM（OpenAI 兼容接口），与执行端 CLI 解耦。
    "models": [],
    # 后端 Agent 实际调用的模型 id；空值表示用第一个启用的模型。
    "agent_model": "",
    "backends": _default_backends(),
    # 数据源是一个「单一对象、类型不同」的配置：``data_source`` 含
    # type/name/enabled + 类型专属字段。目前仅支持 tapd 类型；其他类型
    # （jira/gitlab/github 等）留待后续接入。默认源为「未启用的 TAPD」，
    # 与旧 tapd 单例保持同步（load 阶段把旧单例实质配置迁移进 data_source）。
    "data_source": {
        "type": "tapd",
        "name": "TAPD",
        "enabled": False,
        "tapd": {},
    },
    "tapd": {
        "enabled": False,
        "base_url": "https://api.tapd.cn",
        "workspace_id": "",  # 可逗号分隔多选项目 ID
        "company_id": "",    # 企业 ID，拉取该企业下项目列表必填
        "projects": [],      # 已拉取/已选项目缓存 [{id,name,pretty_name,company_id}]，供企业下拉复用
        "api_user": "",
        "api_password": "",
        "webhook_secret": "",
        "entity_types": ["story", "bug"],
        "pull_statuses": [],
        "status_on_success": "resolved",
        "status_on_failure": "",
        "current_user": "taskagent",
        "auto_pull": False,   # 定时拉取开关：默认关闭，独立于数据源总开关
        "interval_minutes": 5,
        "exec_mode": "dry-run",
    },
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8010
    project_roots: tuple[Path, ...] = (Path("~/Desktop/code/projects"),)
    codebuddy_command: str = "codebuddy"
    codebuddy_model: str | None = None
    timeout_s: float = 600.0
    max_concurrent: int = 1
    project_map_file: Path | None = None  # project-name/alias -> folder JSON
    fs_root: Path | None = None  # read-only folder browser boundary (defaults to HOME)
    commit_rules_file: Path | None = None  # extra commit-ignore patterns (one per line)
    auto_sync: bool = True  # copy apply changes back into the main repo worktree
    available_models: tuple[str, ...] = ()  # allowed model ids for per-task selection

    @classmethod
    def from_env(cls) -> Settings:
        roots = tuple(
            Path(item).expanduser()
            for item in os.environ.get("TASKAGENT_PROJECT_ROOTS", "~/Desktop/code/projects").split(":")
            if item.strip()
        )
        return cls(
            data_dir=Path(os.environ.get("TASKAGENT_DATA_DIR", "data")),
            host=os.environ.get("TASKAGENT_HOST", "127.0.0.1"),
            port=int(os.environ.get("TASKAGENT_PORT", "8010")),
            project_roots=roots,
            codebuddy_command=os.environ.get("CODEBUDDY_COMMAND", "codebuddy"),
            codebuddy_model=os.environ.get("CODEBUDDY_MODEL") or None,
            timeout_s=float(os.environ.get("CODEBUDDY_TIMEOUT", "600")),
            max_concurrent=max(1, int(os.environ.get("TASKAGENT_MAX_CONCURRENT", "1"))),
            project_map_file=(
                Path(item).expanduser()
                if (item := os.environ.get("TASKAGENT_PROJECT_MAP_FILE"))
                else None  # api/cli default to data_dir / "projects.json"
            ),
            fs_root=Path(
                os.environ.get("TASKAGENT_FS_ROOT", str(Path.home()))
            ).expanduser(),
            commit_rules_file=(
                Path(item).expanduser()
                if (item := os.environ.get("TASKAGENT_COMMIT_RULES_FILE"))
                else None  # api defaults to data_dir / "commit-ignore.txt"
            ),
            auto_sync=os.environ.get("TASKAGENT_AUTO_SYNC", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            available_models=tuple(
                m.strip()
                for m in os.environ.get("TASKAGENT_MODELS", "").split(",")
                if m.strip()
            ),
        )

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def web_dir(self) -> Path:
        return Path(__file__).parent / "web"

    @property
    def browse_root(self) -> Path:
        return (self.fs_root or Path.home()).expanduser()

    @property
    def project_map_seed(self) -> Path:
        return Path(__file__).parent / "projects.seed.json"

    @property
    def commit_rules_path(self) -> Path:
        return (
            self.commit_rules_file
            if self.commit_rules_file is not None
            else self.data_dir / "commit-ignore.txt"
        )

    def model_choices(
        self,
        discovered: list[dict[str, Any]] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Model options for the UI: ``[{id, label, ...}, ...]``.

        ``discovered`` is the live list from ``Executor.list_models`` — either
        structured ``[{id, label, vendor, context}]`` entries (local cache) or
        bare id strings (``--help`` fallback). ``TASKAGENT_MODELS`` narrows it
        further when set. Falls back to ``available_models`` when ``discovered``
        is None (older callers).
        """
        if discovered is None:
            return [{"id": mid, "label": mid} for mid in self.available_models]

        # Normalise both structured dicts and bare id strings to dict entries.
        entries: list[dict[str, Any]] = []
        for item in discovered:
            if isinstance(item, str):
                entries.append({"id": item, "label": item})
            elif isinstance(item, dict) and item.get("id"):
                entries.append(item)

        if self.available_models:
            # TASKAGENT_MODELS is an allow-list: keep only ids the operator listed.
            allowed = set(self.available_models)
            entries = [e for e in entries if e["id"] in allowed]
            if not entries:
                entries = [{"id": mid, "label": mid} for mid in self.available_models]
        return entries

    @property
    def default_model(self) -> str | None:
        """Env-level default: CODEBUDDY_MODEL, else the first allowed option.

        Note: the UI-facing default model is the default backend's ``model``
        field (see ``/api/config``), not this env-derived value. This property
        only seeds the initial backend profile before the UI writes settings.
        """
        return self.codebuddy_model or (
            self.available_models[0] if self.available_models else None
        )

    @property
    def ui_settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def load_ui_settings(self) -> dict[str, Any]:
        """Return UI-managed settings, starting from env values on first run."""
        backends = _default_backends()
        # env-derived values seed the codebuddy backend until the UI writes a file
        backends["codebuddy"]["command"] = self.codebuddy_command
        backends["codebuddy"]["model"] = self.codebuddy_model or ""
        backends["codebuddy"]["auto_sync"] = self.auto_sync
        data: dict[str, Any] = {
            **UI_SETTINGS_DEFAULTS,
            "backends": backends,
            "codebuddy_command": self.codebuddy_command,
            "default_model": self.default_model or "",
            "max_concurrent": self.max_concurrent,
            "auto_sync": self.auto_sync,
            "data_source": dict(UI_SETTINGS_DEFAULTS["data_source"]),
            "tapd": dict(UI_SETTINGS_DEFAULTS["tapd"]),
            "models": [],
        }
        path = self.ui_settings_path
        saved: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    saved = loaded
                if saved:
                    for key, value in saved.items():
                        if key == "backends":
                            # The saved map is authoritative: it defines the full
                            # set of backends (added OR removed in the UI). Merge
                            # each profile onto a template so new fields survive.
                            # No backend is privileged: any may be removed or
                            # disabled; the default backend is tracked by
                            # ``executor``, not by a hard-coded id.
                            if isinstance(value, dict):
                                merged: dict[str, dict[str, Any]] = {}
                                for backend_id, cfg in value.items():
                                    if not isinstance(backend_id, str) or not backend_id.strip():
                                        continue
                                    if not isinstance(cfg, dict):
                                        continue
                                    profile = _backend_profile()
                                    profile.update(cfg)
                                    profile["label"] = profile.get("label") or backend_id.strip()
                                    merged[backend_id.strip()] = profile
                                data["backends"] = merged
                        elif key == "tapd" and isinstance(value, dict):
                            data[key].update(value)
                            data[key].pop("nick", None)  # 已移除：拉项目改走 company_id
                        elif key == "data_source" and isinstance(value, dict):
                            data[key] = normalise_data_source(value)
                        elif key == "models" and isinstance(value, list):
                            models: list[dict[str, Any]] = []
                            seen_ids: set[str] = set()
                            for item in value:
                                if not isinstance(item, dict):
                                    continue
                                model = normalise_model(item)
                                if not model["id"] or model["id"] in seen_ids:
                                    continue
                                seen_ids.add(model["id"])
                                models.append(model)
                            data["models"] = models
                        elif key in data:
                            data[key] = value
            except (OSError, ValueError):
                pass  # corrupt/unreadable file -> fall back to defaults
        # 后端 Agent 的默认模型必须指向一份已保存的配置；空值表示用第一个启用的。
        _sync_agent_model(data)
        # Migrate legacy flat fields into backends when the file predates them.
        if isinstance(saved, dict) and "backends" not in saved:
            data["backends"]["codebuddy"]["command"] = data.get("codebuddy_command") or "codebuddy"
            data["backends"]["dsh"]["command"] = data.get("dsh_path") or "dsh"
            data["backends"]["codebuddy"]["model"] = data.get("default_model") or ""
            data["backends"]["codebuddy"]["auto_sync"] = bool(data.get("auto_sync", True))
        # Migrate legacy single ``tapd`` into ``data_source`` when the saved
        # data_source has no substantive tapd payload yet (i.e. never configured)
        # AND the tapd config is substantive (enabled, or has
        # workspace/company/api_user). A blank default tapd does not migrate, so a
        # round-trip that never touches tapd keeps data_source.tapd empty.
        legacy = data.get("tapd") or {}
        substantive_legacy = (
            legacy.get("enabled")
            or str(legacy.get("workspace_id") or "").strip()
            or str(legacy.get("company_id") or "").strip()
            or str(legacy.get("api_user") or "").strip()
        )
        ds = data.get("data_source") or {}
        ds_tapd = ds.get("tapd") if isinstance(ds.get("tapd"), dict) else {}
        ds_substantive = (
            bool(ds.get("enabled"))
            or str(ds_tapd.get("workspace_id") or "").strip()
            or str(ds_tapd.get("company_id") or "").strip()
            or str(ds_tapd.get("api_user") or "").strip()
        )
        if substantive_legacy and not ds_substantive:
            data["data_source"] = tapd_to_data_source(legacy)
        # Desktop-app CLIs are usually not on PATH: resolve the bundle path so a
        # fresh install works without the user pasting an absolute path.
        for backend in data.get("backends", {}).values():
            if isinstance(backend, dict) and backend.get("command"):
                backend["command"] = discover_command(str(backend["command"]))
        for flat_key in ("codebuddy_command", "dsh_path"):
            if data.get(flat_key):
                data[flat_key] = discover_command(str(data[flat_key]))
        return data

    def save_ui_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Merge and persist UI-managed settings; blank secrets keep the old value."""
        data = self.load_ui_settings()
        for key, value in payload.items():
            if key == "backends" and isinstance(value, dict):
                # The submitted map is authoritative: backends absent from it
                # have been removed in the UI. Rebuild from persisted profiles
                # so edited fields on surviving backends are preserved. No
                # backend is privileged or force-enabled.
                merged: dict[str, dict[str, Any]] = {}
                for backend_id, cfg in value.items():
                    if not isinstance(backend_id, str) or not backend_id.strip():
                        continue
                    if not isinstance(cfg, dict):
                        continue
                    bid = backend_id.strip()
                    profile = dict(data["backends"].get(bid) or _backend_profile())
                    incoming = dict(cfg)
                    # blank command keeps the saved value (avoids wiping a path)
                    if not incoming.get("command"):
                        incoming["command"] = profile.get("command", "")
                    profile.update(incoming)
                    merged[bid] = profile
                data["backends"] = merged
                # default backend: keep existing, or first available if the
                # current one was removed/never set. Empty backends -> empty.
                if data.get("executor") not in data["backends"]:
                    data["executor"] = next(iter(data["backends"]), "")
            elif key == "tapd" and isinstance(value, dict):
                tapd = dict(value)
                # Blank password keeps the stored value (UI masks it).
                if not tapd.get("api_password"):
                    tapd["api_password"] = data["tapd"].get("api_password", "")
                # Lists must not arrive as strings from the UI.
                for list_key in ("entity_types", "pull_statuses"):
                    val = tapd.get(list_key)
                    if isinstance(val, str):
                        tapd[list_key] = [part.strip() for part in val.split(",") if part.strip()]
                    elif not isinstance(val, list):
                        tapd[list_key] = list(data["tapd"].get(list_key, []))
                data["tapd"].update(tapd)
            elif key == "data_source" and isinstance(value, dict):
                # 单一对象：完整接管。空白密码保留旧值（UI 掩码显示）。
                src = normalise_data_source(value)
                src_type = src["type"]
                tapd = src.get("tapd") or {}
                if src_type == "tapd":
                    if not tapd.get("api_password"):
                        # 回退源：旧 data_source.tapd → legacy tapd 单例（本次 save
                        # 已合并的结果）→ 磁盘旧值。逐级取首个非空密码。
                        old_tapd = data.get("data_source", {}).get("tapd", {})
                        if isinstance(old_tapd, dict) and old_tapd.get("api_password"):
                            tapd["api_password"] = old_tapd["api_password"]
                        else:
                            tapd["api_password"] = data["tapd"].get("api_password", "")
                    for list_key in ("entity_types", "pull_statuses"):
                        val = tapd.get(list_key)
                        if isinstance(val, str):
                            tapd[list_key] = [p.strip() for p in val.split(",") if p.strip()]
                        elif not isinstance(val, list):
                            tapd[list_key] = []
                    src["tapd"] = tapd
                data["data_source"] = src
            elif key == "models" and isinstance(value, list):
                # 完整接管：模型配置由 UI 维护，空 api_key 表示「保持原值」
                # （UI 对秘钥做掩码，不回传明文），按 id 继承已保存的秘钥。
                old = {
                    str(m.get("id")): m
                    for m in (data.get("models") or [])
                    if isinstance(m, dict)
                }
                models: list[dict[str, Any]] = []
                seen_model_ids: set[str] = set()
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    model = normalise_model(item)
                    if not model["id"] or model["id"] in seen_model_ids:
                        continue
                    seen_model_ids.add(model["id"])
                    if not model["api_key"]:
                        model["api_key"] = str(
                            (old.get(model["id"]) or {}).get("api_key") or ""
                        )
                    models.append(model)
                data["models"] = models
            elif key in data:
                data[key] = value
        _sync_agent_model(data)
        data["max_concurrent"] = max(1, int(data.get("max_concurrent", 1) or 1))
        # Normalise tapd values.
        tapd = data["tapd"]
        tapd.pop("nick", None)  # 旧配置兼容：拉项目已改为只需 company_id
        # 项目缓存：用于企业 ID 下拉与「已填项目」列表；去重、清洗字段。
        projects: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for row in tapd.get("projects") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "").strip()
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            projects.append({
                "id": pid,
                "name": str(row.get("name") or "").strip(),
                "pretty_name": str(row.get("pretty_name") or "").strip(),
                "company_id": str(row.get("company_id") or "").strip(),
            })
        tapd["projects"] = projects
        # workspace_id 允许逗号分隔的多选项目 ID，统一格式避免脏数据。
        tapd["workspace_id"] = ",".join(
            part.strip()
            for part in str(tapd.get("workspace_id") or "").replace("，", ",").split(",")
            if part.strip()
        )
        tapd["enabled"] = bool(tapd.get("enabled", False))
        tapd["auto_pull"] = bool(tapd.get("auto_pull", False))
        tapd["interval_minutes"] = max(1, int(tapd.get("interval_minutes", 5) or 5))
        tapd["exec_mode"] = "apply" if tapd.get("exec_mode") == "apply" else "dry-run"
        tapd["entity_types"] = [e for e in tapd.get("entity_types", []) if e in {"story", "bug", "task"}] or ["story", "bug"]
        # 保持 legacy 单例 `tapd` 与 data_source 的 tapd 载荷一致，让现有
        # 调度/回填/拉取循环无需改动即可继续工作。仅当本次 payload 显式提交了
        # data_source、且其 tapd 载荷「实质」配置过（enabled / workspace /
        # company / api_user 任一非空）才反向同步覆盖 tapd 单例；否则保持 tapd
        # 单例独立（兼容旧客户端只改 tapd 的路径），避免空 data_source 把
        # tapd 单例的实质配置冲掉。data_source 密码为空时继承 tapd 单例已合并
        # 的密码（避免 UI 掩码提交空密码把旧密码冲掉）。
        if "data_source" in payload:
            ds = data.get("data_source") or {}
            ds_tapd = ds.get("tapd") if isinstance(ds.get("tapd"), dict) else {}
            substantive = (
                bool(ds.get("enabled"))
                or str(ds_tapd.get("workspace_id") or "").strip()
                or str(ds_tapd.get("company_id") or "").strip()
                or str(ds_tapd.get("api_user") or "").strip()
            )
            if substantive:
                merged_tapd = dict(ds_tapd)
                if not merged_tapd.get("api_password"):
                    merged_tapd["api_password"] = data["tapd"].get("api_password", "")
                merged_tapd["enabled"] = bool(ds.get("enabled", False))
                data["tapd"] = merged_tapd
        # Normalise backends: valid ids, sane timeouts. No backend is forced on.
        for backend_id, cfg in data["backends"].items():
            cfg["label"] = str(cfg.get("label") or "") or backend_id
            cfg["enabled"] = bool(cfg.get("enabled", False))
            cfg["command"] = str(cfg.get("command") or "")
            cfg["model"] = str(cfg.get("model") or "")
            cfg["timeout_s"] = max(1, int(cfg.get("timeout_s", 600) or 600))
            cfg["auto_sync"] = bool(cfg.get("auto_sync", True))
        # Legacy flat fields are only authoritative when the payload has no
        # ``backends`` key (i.e. a pre-multi-backend client). Otherwise the
        # backends map is the source of truth and flat fields are derived from
        # it below, never the reverse.
        if "backends" not in payload:
            for flat_key, backend_id in {
                "codebuddy_command": "codebuddy",
                "dsh_path": "dsh",
            }.items():
                if backend_id in data["backends"] and flat_key in payload and payload[flat_key]:
                    data["backends"][backend_id]["command"] = payload[flat_key]
            if "default_model" in payload and payload["default_model"] and "codebuddy" in data["backends"]:
                data["backends"]["codebuddy"]["model"] = payload["default_model"]
            if "auto_sync" in payload and "codebuddy" in data["backends"]:
                data["backends"]["codebuddy"]["auto_sync"] = bool(payload["auto_sync"])
        # Reflect backends into flat fields so every API shape stays consistent.
        data["codebuddy_command"] = data["backends"].get("codebuddy", {}).get("command", "codebuddy")
        data["dsh_path"] = data["backends"].get("dsh", {}).get("command", "")
        data["default_model"] = data["backends"].get("codebuddy", {}).get("model", "")
        data["auto_sync"] = bool(data["backends"].get("codebuddy", {}).get("auto_sync", True))
        # default backend: keep existing, else first available; empty -> empty.
        if data.get("executor") not in data["backends"]:
            data["executor"] = next(iter(data["backends"]), "")
        path = self.ui_settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return data
