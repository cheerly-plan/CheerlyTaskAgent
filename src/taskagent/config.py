"""Runtime settings, overridable by environment. No secrets in the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
