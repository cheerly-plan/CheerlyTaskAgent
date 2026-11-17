"""Build the executor registry from persisted UI settings.

Kept in its own module to avoid a config <-> executor import cycle: the from_env
Settings object supplies the shared worktree/backup roots, while the UI-managed
``backends`` profile supplies per-type command / model / timeout / auto_sync.
"""

from __future__ import annotations

from pathlib import Path

from .commitfilter import load_extra_patterns
from .config import Settings
from .executor import (
    BACKEND_CODEBUDDY,
    BACKEND_DSH,
    BACKEND_WORKBUDDY,
    CodeBuddyExecutor,
    DshExecutor,
    ExecutorRegistry,
    WorkBuddyExecutor,
)

_EXECUTOR_CLASSES = {
    BACKEND_CODEBUDDY: CodeBuddyExecutor,
    BACKEND_WORKBUDDY: WorkBuddyExecutor,
    BACKEND_DSH: DshExecutor,
}


def build_registry(
    settings: Settings,
    *,
    ui: dict | None = None,
    sync_ignore_patterns: tuple[str, ...] | None = None,
) -> ExecutorRegistry:
    """Create one executor per backend type from settings.

    An executor is always created for every known type (so a queued legacy item
    can still resolve its backend). The profile's ``enabled`` flag is surfaced
    to the UI; runtime dispatch falls back to codebuddy for a disabled/missing
    backend rather than crashing.
    """
    ui = ui if ui is not None else settings.load_ui_settings()
    profiles = ui.get("backends") or {}
    patterns = (
        sync_ignore_patterns
        if sync_ignore_patterns is not None
        else tuple(load_extra_patterns(settings.commit_rules_path))
    )
    common = dict(
        worktrees_dir=settings.worktrees_dir,
        backups_dir=settings.data_dir / "sync-backups",
        sync_ignore_patterns=patterns,
    )
    executors: dict[str, object] = {}
    for backend_id, cls in _EXECUTOR_CLASSES.items():
        profile = profiles.get(backend_id) or {}
        command = profile.get("command") or ""
        if not command and backend_id == BACKEND_CODEBUDDY:
            command = settings.codebuddy_command
        executors[backend_id] = cls(
            command=command,
            timeout_s=float(profile.get("timeout_s", settings.timeout_s) or settings.timeout_s),
            model=(profile.get("model") or None),
            auto_sync=bool(profile.get("auto_sync", settings.auto_sync)),
            **common,
        )
    return ExecutorRegistry(executors)  # type: ignore[arg-type]


def describe_backends(settings: Settings, registry: ExecutorRegistry | None = None) -> list[dict]:
    """UI-facing backend list: id, label, command and enabled flag."""
    registry = registry or build_registry(settings)
    ui = settings.load_ui_settings()
    profiles = ui.get("backends") or {}
    result = []
    for info in registry.describe():
        profile = profiles.get(info["id"]) or {}
        result.append({
            **info,
            "enabled": bool(profile.get("enabled", info["id"] == BACKEND_CODEBUDDY)),
        })
    return result
