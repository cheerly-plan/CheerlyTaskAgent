"""Runtime settings, overridable by environment. No secrets in the repo."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Model IDs exactly as advertised by `codebuddy --help` (verified 2026-11-17).
# CLI only publishes raw IDs, no official human names, so the label is the ID
# itself — no hand-made translations that could drift from what argv accepts.
# The first three are routing aliases whose concrete model may change over
# time; the rest are concrete model IDs.
CODEBUDDY_MODELS: tuple[str, ...] = (
    "fast-model",
    "balanced-model",
    "deep-model",
    "hy4std",
    "hy4flash",
    "hy4flash-free",
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "kimi-k3.1-pro",
    "kimi-k3-1",
    "kimi-k2.8-preview",
    "minimax-m3.1-flash",
    "step-5-preview",
    "mimo-v2.6-pro",
    "space-bunny",
)

# Settings managed from the UI "系统设置" tab. Stored in <data_dir>/settings.json
# so the values survive restarts; env-derived values are used as the initial
# defaults until the file is written. DSH / WorkBuddy are execution backends
# reserved for later, so their paths are kept here but not yet dispatched to.
def _default_backends() -> dict[str, dict[str, Any]]:
    """Per-type execution backend configs, each a complete independent profile."""
    return {
        "codebuddy": {
            "enabled": True,  # always available as the safe fallback
            "command": "codebuddy",
            "model": "",
            "timeout_s": 600,
            "auto_sync": True,
        },
        "workbuddy": {
            "enabled": False,
            "command": "workbuddy",
            "model": "",
            "timeout_s": 600,
            "auto_sync": True,
        },
        "dsh": {
            "enabled": False,
            "command": "dsh",
            "model": "",
            "timeout_s": 600,
            "auto_sync": True,
        },
    }


UI_SETTINGS_DEFAULTS: dict[str, Any] = {
    "executor": "codebuddy",  # default backend for new tasks (legacy key, kept for /api/config)
    "codebuddy_command": "codebuddy",
    "workbuddy_cli_path": "",
    "dsh_path": "",
    "default_model": "",
    "max_concurrent": 1,
    "auto_sync": True,
    "backends": _default_backends(),
    "imap": {"host": "", "port": 993, "user": "", "password": "", "use_ssl": True},
    "mail_poll": {"enabled": False, "interval_minutes": 5, "exec_mode": "dry-run"},
    "tapd": {
        "enabled": False,
        "base_url": "https://api.tapd.cn",
        "workspace_id": "",
        "api_user": "",
        "api_password": "",
        "webhook_secret": "",
        "entity_types": ["story", "bug"],
        "pull_statuses": [],
        "status_on_success": "resolved",
        "status_on_failure": "",
        "current_user": "taskagent",
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

    @property
    def model_choices(self) -> list[dict[str, str]]:
        """Model options for the UI: ``[{id, label}, ...]``.

        When ``TASKAGENT_MODELS`` is set, only those ids are offered (in that
        order). Label is the raw CLI ID — CodeBuddy publishes no human names.
        """
        ids = self.available_models or CODEBUDDY_MODELS
        return [{"id": mid, "label": mid} for mid in ids]

    @property
    def default_model(self) -> str | None:
        """The per-task default: CODEBUDDY_MODEL env, else first allowed option."""
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
            "imap": dict(UI_SETTINGS_DEFAULTS["imap"]),
            "mail_poll": dict(UI_SETTINGS_DEFAULTS["mail_poll"]),
            "tapd": dict(UI_SETTINGS_DEFAULTS["tapd"]),
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
                        if key in {"imap", "mail_poll", "tapd", "backends"} and isinstance(value, dict):
                            if key == "backends":
                                # deep merge each backend so new fields get defaults
                                for backend_id, cfg in value.items():
                                    if backend_id in data["backends"] and isinstance(cfg, dict):
                                        data["backends"][backend_id].update(cfg)
                            else:
                                data[key].update(value)
                        elif key in data:
                            data[key] = value
            except (OSError, ValueError):
                pass  # corrupt/unreadable file -> fall back to defaults
        # Migrate legacy flat fields into backends when the file predates them.
        if isinstance(saved, dict) and "backends" not in saved:
            data["backends"]["codebuddy"]["command"] = data.get("codebuddy_command") or "codebuddy"
            data["backends"]["workbuddy"]["command"] = data.get("workbuddy_cli_path") or "workbuddy"
            data["backends"]["dsh"]["command"] = data.get("dsh_path") or "dsh"
            data["backends"]["codebuddy"]["model"] = data.get("default_model") or ""
            data["backends"]["codebuddy"]["auto_sync"] = bool(data.get("auto_sync", True))
        return data

    def save_ui_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Merge and persist UI-managed settings; blank IMAP password keeps the old one."""
        data = self.load_ui_settings()
        for key, value in payload.items():
            if key == "backends" and isinstance(value, dict):
                for backend_id, cfg in value.items():
                    if backend_id in data["backends"] and isinstance(cfg, dict):
                        incoming = dict(cfg)
                        # blank command keeps the saved value (avoids wiping a path)
                        if not incoming.get("command"):
                            incoming["command"] = data["backends"][backend_id].get("command", "")
                        data["backends"][backend_id].update(incoming)
                # Legacy flat fields in the same payload win over stale backend
                # defaults (older clients submit both). Let them flow inward.
                flat_to_backend = {
                    "codebuddy_command": "codebuddy",
                    "workbuddy_cli_path": "workbuddy",
                    "dsh_path": "dsh",
                }
                for flat_key, backend_id in flat_to_backend.items():
                    if payload.get(flat_key):
                        data["backends"][backend_id]["command"] = payload[flat_key]
                if payload.get("default_model"):
                    data["backends"]["codebuddy"]["model"] = payload["default_model"]
                if "auto_sync" in payload:
                    data["backends"]["codebuddy"]["auto_sync"] = bool(payload["auto_sync"])
                # codebuddy is always the safe fallback; never fully disable it.
                data["backends"]["codebuddy"]["enabled"] = True
                # keep legacy flat keys in sync for older clients / /api/config
                data["codebuddy_command"] = data["backends"]["codebuddy"].get("command", "codebuddy")
                data["workbuddy_cli_path"] = data["backends"]["workbuddy"].get("command", "")
                data["dsh_path"] = data["backends"]["dsh"].get("command", "")
                data["default_model"] = data["backends"]["codebuddy"].get("model", "")
                data["auto_sync"] = bool(data["backends"]["codebuddy"].get("auto_sync", True))
                data["executor"] = data.get("executor") or "codebuddy"
            elif key == "imap" and isinstance(value, dict):
                imap = dict(value)
                # The UI masks the stored password; an empty submission means
                # "unchanged" instead of clearing it.
                if not imap.get("password"):
                    imap["password"] = data["imap"].get("password", "")
                data["imap"].update(imap)
            elif key == "mail_poll" and isinstance(value, dict):
                data["mail_poll"].update(value)
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
            elif key in data:
                data[key] = value
        data["max_concurrent"] = max(1, int(data.get("max_concurrent", 1) or 1))
        # Normalise mail_poll values to sane bounds.
        poll = data["mail_poll"]
        poll["enabled"] = bool(poll.get("enabled", False))
        poll["interval_minutes"] = max(1, int(poll.get("interval_minutes", 5) or 5))
        poll["exec_mode"] = "apply" if poll.get("exec_mode") == "apply" else "dry-run"
        # Normalise tapd values.
        tapd = data["tapd"]
        tapd["enabled"] = bool(tapd.get("enabled", False))
        tapd["interval_minutes"] = max(1, int(tapd.get("interval_minutes", 5) or 5))
        tapd["exec_mode"] = "apply" if tapd.get("exec_mode") == "apply" else "dry-run"
        tapd["entity_types"] = [e for e in tapd.get("entity_types", []) if e in {"story", "bug", "task"}] or ["story", "bug"]
        # Normalise backends: valid ids, sane timeouts, codebuddy always on.
        for backend_id, cfg in data["backends"].items():
            cfg["enabled"] = True if backend_id == "codebuddy" else bool(cfg.get("enabled", False))
            cfg["command"] = str(cfg.get("command") or "")
            cfg["model"] = str(cfg.get("model") or "")
            cfg["timeout_s"] = max(1, int(cfg.get("timeout_s", 600) or 600))
            cfg["auto_sync"] = bool(cfg.get("auto_sync", True))
        # Final insurance: any explicitly-submitted legacy flat field is the
        # source of truth for its backend command (covers payloads without a
        # ``backends`` key entirely).
        for flat_key, backend_id in {
            "codebuddy_command": "codebuddy",
            "workbuddy_cli_path": "workbuddy",
            "dsh_path": "dsh",
        }.items():
            if flat_key in payload and payload[flat_key]:
                data["backends"][backend_id]["command"] = payload[flat_key]
        if "default_model" in payload and payload["default_model"]:
            data["backends"]["codebuddy"]["model"] = payload["default_model"]
        if "auto_sync" in payload:
            data["backends"]["codebuddy"]["auto_sync"] = bool(payload["auto_sync"])
        # Reflect backends into flat fields so every API shape stays consistent.
        data["codebuddy_command"] = data["backends"]["codebuddy"]["command"]
        data["workbuddy_cli_path"] = data["backends"]["workbuddy"]["command"]
        data["dsh_path"] = data["backends"]["dsh"]["command"]
        data["default_model"] = data["backends"]["codebuddy"]["model"]
        data["auto_sync"] = bool(data["backends"]["codebuddy"]["auto_sync"])
        if data.get("executor") not in data["backends"]:
            data["executor"] = "codebuddy"
        path = self.ui_settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return data
