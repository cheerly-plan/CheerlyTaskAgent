"""Build the executor registry from persisted UI settings.

Kept in its own module to avoid a config <-> executor import cycle: the from_env
Settings object supplies the shared worktree/backup roots, while the UI-managed
``backends`` profile supplies per-type command / model / timeout / auto_sync.

Backends are configuration-driven: any number of them may be added or removed
in the UI, and none is privileged. The default backend (``executor``) is the
runtime fallback for missing/disabled backend ids.
"""

from __future__ import annotations

from .commitfilter import load_extra_patterns
from .config import Settings
from .executor import (
    CodeBuddyExecutor,
    ExecutorRegistry,
    backend_supports_model_selection,
    executor_class,
)


def build_registry(
    settings: Settings,
    *,
    ui: dict | None = None,
    sync_ignore_patterns: tuple[str, ...] | None = None,
) -> ExecutorRegistry:
    """Create one executor per configured backend profile.

    An executor is created for every backend present in settings, so any id a
    queued item refers to can still resolve. The profile's ``enabled`` flag is
    surfaced to the UI; runtime dispatch falls back to the configured default
    backend (``executor``) for a disabled/missing backend rather than crashing.
    """
    ui = ui if ui is not None else settings.load_ui_settings()
    profiles = ui.get("backends") or {}
    default_backend = ui.get("executor") or ""
    patterns = (
        sync_ignore_patterns
        if sync_ignore_patterns is not None
        else tuple(load_extra_patterns(settings.commit_rules_path))
    )
    common = {
        "worktrees_dir": settings.worktrees_dir,
        "backups_dir": settings.data_dir / "sync-backups",
        "sync_ignore_patterns": patterns,
        # P1: every backend shares the same pre-run baseline policy.
        "baseline_sync": settings.baseline_sync,
        "baseline_remote": settings.baseline_remote,
        "fetch_timeout_s": settings.fetch_timeout_s,
    }
    executors: dict[str, CodeBuddyExecutor] = {}
    for backend_id, profile in profiles.items():
        if not isinstance(backend_id, str) or not backend_id.strip():
            continue
        if not isinstance(profile, dict):
            continue
        command = profile.get("command") or ""
        klass = executor_class(backend_id)
        extra: dict[str, object] = {}
        if klass is not CodeBuddyExecutor:
            # Backends with a CLI-specific model protocol (DSH: --patch overlay).
            extra["patch_dir"] = settings.data_dir / "dsh-patches"
        executors[backend_id] = klass(
            backend_id=backend_id,
            label=str(profile.get("label") or "") or backend_id,
            command=command,
            timeout_s=float(profile.get("timeout_s", settings.timeout_s) or settings.timeout_s),
            model=(profile.get("model") or None),
            supports_model_selection=backend_supports_model_selection(backend_id),
            auto_sync=bool(profile.get("auto_sync", settings.auto_sync)),
            **extra,
            **common,
        )
    return ExecutorRegistry(executors, default_backend_id=default_backend)  # type: ignore[arg-type]


def describe_backends(settings: Settings, registry: ExecutorRegistry | None = None) -> list[dict]:
    """UI-facing backend list: id, label, command, enabled flag and its own model list.

    Each backend carries the model ids discovered from its OWN CLI ``--help``
    (``Executor.list_models``), so a backend whose CLI is missing/unknown shows
    an empty list rather than borrowing another backend's models.
    """
    registry = registry or build_registry(settings)
    ui = settings.load_ui_settings()
    profiles = ui.get("backends") or {}
    result = []
    for info in registry.describe():
        profile = profiles.get(info["id"]) or {}
        executor = registry.get(info["id"])
        try:
            raw_models = executor.list_models() if executor is not None else []
        except (OSError, ValueError, RuntimeError):
            raw_models = []
        # One contract for every backend: [{id, label, ...}]. Executors may still
        # return bare id strings (older CLIs discovered via --help).
        models = [
            entry if isinstance(entry, dict) and entry.get("id")
            else {"id": str(entry), "label": str(entry)}
            for entry in raw_models
        ]
        result.append({
            **info,
            "enabled": bool(profile.get("enabled", False)),
            "models": models,
            # False => the CLI cannot override its model at all; the UI then
            # only shows the one pinned in its own profile (``models[0]``).
            "model_selection": bool(getattr(executor, "supports_model_selection", True)),
            # The model this backend falls back to when a task doesn't pick one.
            # Empty string => no explicit default; the CLI's own default (the
            # one flagged ``current`` in ``models``) applies instead.
            "default_model": str(profile.get("model") or ""),
        })
    return result
