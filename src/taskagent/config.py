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
UI_SETTINGS_DEFAULTS: dict[str, Any] = {
    "executor": "codebuddy",
    "codebuddy_command": "codebuddy",
    "workbuddy_cli_path": "",
    "dsh_path": "",
    "default_model": "",
    "max_concurrent": 1,
    "auto_sync": True,
    "imap": {"host": "", "port": 993, "user": "", "password": "", "use_ssl": True},
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
        data: dict[str, Any] = {
            **UI_SETTINGS_DEFAULTS,
            "codebuddy_command": self.codebuddy_command,
            "default_model": self.default_model or "",
            "max_concurrent": self.max_concurrent,
            "auto_sync": self.auto_sync,
            "imap": dict(UI_SETTINGS_DEFAULTS["imap"]),
        }
        path = self.ui_settings_path
        if path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    for key, value in saved.items():
                        if key == "imap" and isinstance(value, dict):
                            data["imap"].update(value)
                        elif key in data:
                            data[key] = value
            except (OSError, ValueError):
                pass  # corrupt/unreadable file -> fall back to defaults
        return data

    def save_ui_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Merge and persist UI-managed settings; blank IMAP password keeps the old one."""
        data = self.load_ui_settings()
        for key, value in payload.items():
            if key == "imap" and isinstance(value, dict):
                imap = dict(value)
                # The UI masks the stored password; an empty submission means
                # "unchanged" instead of clearing it.
                if not imap.get("password"):
                    imap["password"] = data["imap"].get("password", "")
                data["imap"].update(imap)
            elif key in data and key != "imap":
                data[key] = value
        data["max_concurrent"] = max(1, int(data.get("max_concurrent", 1) or 1))
        path = self.ui_settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return data
