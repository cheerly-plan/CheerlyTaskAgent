"""Closed strategy library for CLI dialects.

Every agent CLI differs in four places, and only in these four:

1. **invocation** — how the model is passed on the command line / into config,
2. **models** — where the selectable model list comes from,
3. **capability** — whether a per-run model override really takes effect,
4. **verdict** — what counts as a real, successful execution.

This module deliberately offers a *closed* set of small strategies instead of a
templating language: adding a CLI means composing strategies that already
exist, and exotic dialects (DSH's ``--patch`` overlay) are expressed as a named
builtin strategy rather than user-authored logic. A configuration file can
therefore never grow into a mini-language.

The execution mechanics — process spawn, worktree isolation, git diff/commit,
sync, retries, concurrency — stay in ``executor.py`` and never appear here.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import dsh_profile


# ── shared context ────────────────────────────────────────────────────────────
@dataclass
class DialectContext:
    """Everything a strategy may need, supplied by the executor."""

    command: str
    env: dict[str, str]
    patch_dir: Path
    profile_home: Path
    run_cli: Callable[[Sequence[str]], tuple[int, str, str]]
    model: str | None = None


# ── verdict: what counts as a real execution ─────────────────────────────────
def no_execution_detected(stdout: str) -> bool:
    """Whether the CLI exited 0 without actually running the prompt.

    Evidence-based: any non-zero token usage, tool call, or text output means
    work happened. Empty output / all-zero usage means the CLI produced a
    "success" envelope without doing anything.
    """
    text = (stdout or "").strip()
    if not text:
        return True

    token_pattern = re.compile(r'"(?:input_tokens|output_tokens|total_tokens)"\s*:\s*(\d+)')
    tokens = any(int(n) > 0 for n in token_pattern.findall(text))
    if tokens:
        return False

    camel = re.compile(
        r'"(?:inputTokens|outputTokens|totalTokens|promptTokens|completionTokens)"\s*:\s*(\d+)'
    )
    if any(int(n) > 0 for n in camel.findall(text)):
        return False

    nested = re.compile(r'"(?:usage|tokenUsage)"\s*:\s*\{([^}]*)\}', re.DOTALL)
    for chunk in nested.findall(text):
        if any(int(n) > 0 for n in re.findall(r'"?\w*[Tt]okens"?\s*:\s*(\d+)', chunk)):
            return False

    if re.search(r'"(?:tool_calls|toolCalls)"\s*:\s*\[', text):
        return False
    if re.search(r'"type"\s*:\s*"function_call"', text):
        return False

    result = re.search(r'"(?:result|text|content)"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    return not (result and result.group(1).strip())


@dataclass
class StrictVerdict:
    """Default policy: a suspicious success is treated as a failure.

    Being permissive here would silently accept runs that never happened (or
    that used the wrong model), which is exactly the class of bug the scheduler
    must never hide. False failures are visible and retryable; silent wrong
    results are not.
    """

    auth_markers: tuple[str, ...] = (
        "authentication required",
        "please use /login",
        "not logged in",
        "invalid api key",
        "401 unauthorized",
    )

    def decide(
        self, *, returncode: int, stdout: str, stderr: str, label: str
    ) -> tuple[bool, str | None]:
        low = (stderr or "").lower()
        if any(marker in low for marker in self.auth_markers):
            return False, f"{label} 未登录或凭据无效：请在终端运行 CLI 完成登录后重试"
        if returncode != 0:
            return False, f"exit code {returncode}"
        if no_execution_detected(stdout):
            # stdout is empty, so stderr is usually the only clue (EPERM, bad
            # flag, missing config). Surface it instead of a generic message.
            detail = next((line.strip() for line in (stderr or "").splitlines() if line.strip()), "")
            message = f"{label} 未执行提示词（空结果/0 token）"
            if detail:
                message += f"：{detail[:300]}"
            return False, message + "；请查看报告 stdout"
        return True, None


@dataclass
class NdjsonStrictVerdict(StrictVerdict):
    """Strict verdict for CLIs that stream newline-delimited run events.

    DSH emits ``{"type":"session"|"status"|...}`` lines. A run counts as done
    only when the event stream shows real work AND the turn did not end in an
    error; an error turn carries a message we can surface verbatim.
    """

    def decide(
        self, *, returncode: int, stdout: str, stderr: str, label: str
    ) -> tuple[bool, str | None]:
        low = (stderr or "").lower()
        if any(marker in low for marker in self.auth_markers):
            return False, f"{label} 未登录或凭据无效：请在终端运行 CLI 完成登录后重试"

        executed = False
        turn_error: str | None = None
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "message" or kind in {"tool_call", "tool_result", "function_call"}:
                executed = True
            elif kind == "status":
                reason = event.get("reason")
                if event.get("phase") == "turn_end" and isinstance(reason, dict):
                    if reason.get("kind") == "error":
                        err = reason.get("error") or {}
                        turn_error = str(err.get("message") or "turn ended with error")
                    else:
                        executed = True

        if returncode != 0:
            detail = next((l.strip() for l in (stderr or "").splitlines() if l.strip()), "")
            return False, f"exit code {returncode}" + (f"：{detail[:300]}" if detail else "")
        if turn_error:
            return False, f"{label} 执行失败：{turn_error[:300]}"
        if not executed:
            detail = next((l.strip() for l in (stderr or "").splitlines() if l.strip()), "")
            message = f"{label} 未执行提示词（无有效事件）"
            if detail:
                message += f"：{detail[:300]}"
            return False, message + "；请查看报告 stdout"
        return True, None


# ── model discovery ──────────────────────────────────────────────────────────
class ModelCatalog(Protocol):
    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]: ...


@dataclass
class StaticCatalog:
    models: list[dict[str, Any]] = field(default_factory=list)

    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]:
        return [dict(m) for m in self.models]


@dataclass
class ChainCatalog:
    """First non-empty source wins — richest first, then progressively poorer."""

    catalogs: tuple[ModelCatalog, ...]

    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]:
        for catalog in self.catalogs:
            found = catalog.discover(ctx)
            if found:
                return found
        return []


@dataclass
class HelpRegexCatalog:
    """Parse the model ids a CLI advertises in ``--help``."""

    pattern: str = r"--model\s+<[^>]*>.*?\(([^)]*)\)"
    split: str = r"[,\s]+"

    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]:
        _code, help_text, _err = ctx.run_cli(["--help"])
        match = re.search(self.pattern, help_text or "", re.DOTALL)
        if not match:
            return []
        ids = [
            token.strip()
            for token in re.split(self.split, match.group(1))
            if token.strip() and not token.strip().startswith("--")
        ]
        return [
            {"id": mid, "label": mid}
            for mid in ids
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", mid)
        ]


@dataclass
class LocalCacheCatalog:
    """Read a CLI's local model cache (structured JSON, no CLI round-trip)."""

    roots: tuple[str, ...] = ("~/.codebuddy",)

    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]:
        candidates: list[Path] = []
        home = Path.home()
        for root in self.roots:
            base = Path(root).expanduser() if not root.startswith("~") else home / root[2:]
            models_json = base / "models.json"
            if models_json.is_file():
                candidates.append(models_json)
            storage = base / "local_storage"
            if storage.is_dir():
                try:
                    candidates.extend(
                        sorted(
                            storage.glob("*.info"),
                            key=lambda p: p.stat().st_mtime,
                            reverse=True,
                        )
                    )
                except OSError:
                    pass

        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            found = _models_from_any_payload(data)
            if found:
                return found
        return []


def _models_from_any_payload(data: Any) -> list[dict[str, Any]]:
    """Unwrap the cache shapes CLIs use: plain dict, per-user list, gzip string."""
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("data"), dict):
                found = cache_payload_models(item["data"])
                if found:
                    return found
        return []
    if isinstance(data, str):
        decoded = decode_gzip_json(data)
        return cache_payload_models(decoded) if isinstance(decoded, dict) else []
    if isinstance(data, dict):
        return cache_payload_models(data)
    return []


def decode_gzip_json(text: str) -> Any:
    """Decode a base64 gzip JSON string; None when it isn't one."""
    if not text.startswith("H4sI"):
        return None
    try:
        return json.loads(gzip.decompress(base64.b64decode(text)))
    except (ValueError, OSError):
        return None


def cli_model_allowlist(data: dict[str, Any]) -> list[str]:
    """Ids the cli agent declares usable, in display order (``/model list``)."""
    agents = data.get("agents")
    if not isinstance(agents, list):
        return []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        tags = agent.get("tags")
        is_cli = agent.get("name") == "cli" or (isinstance(tags, list) and "cli" in tags)
        models = agent.get("models")
        if is_cli and isinstance(models, list):
            return list(
                dict.fromkeys(m.strip() for m in models if isinstance(m, str) and m.strip())
            )
    return []


def cache_payload_models(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured models from a cache payload dict."""
    models = data.get("availableModels") or data.get("models")
    if not isinstance(models, list):
        return []
    by_id: dict[str, dict[str, Any]] = {}
    for m in models:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid.strip():
            continue
        mid = mid.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", mid):
            continue
        label = str(m.get("name") or "").strip() or mid
        entry: dict[str, Any] = {"id": mid, "label": label}
        vendor = m.get("vendor")
        if isinstance(vendor, str) and vendor.strip():
            entry["vendor"] = vendor.strip()
        context = m.get("maxInputTokens")
        if isinstance(context, int):
            entry["context"] = context
        by_id.setdefault(mid, entry)

    allowed = cli_model_allowlist(data)
    if allowed:
        return [by_id.get(mid, {"id": mid, "label": mid}) for mid in allowed]
    return list(by_id.values())


def dedupe_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse aliases that share the same label and context window."""
    seen: dict[tuple[str, Any], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for m in models:
        key = (str(m.get("label", "")).strip().lower(), m.get("context"))
        kept = seen.get(key)
        if kept is None:
            entry = dict(m)
            seen[key] = entry
            result.append(entry)
        else:
            kept.setdefault("aliases", []).append(m["id"])
            if m.get("current"):
                kept["current"] = True
    return result


def current_model_from_sessions(root: Path) -> str | None:
    """Most recent model recorded in a CLI's session logs, if any."""
    projects = root / "projects"
    if not projects.is_dir():
        return None
    try:
        files = sorted(
            projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return None
    for path in files[:3]:
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 256 * 1024))
                tail = fh.read().decode("utf-8", errors="ignore")
        except OSError:
            continue
        found: str | None = None
        for line in tail.splitlines():
            if "requestModelId" not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            pd = rec.get("providerData")
            if isinstance(pd, dict):
                mid = pd.get("requestModelId") or pd.get("requestModelName")
                if isinstance(mid, str) and mid.strip():
                    found = mid.strip()
        if found:
            return found
    return None


def mark_current(models: list[dict[str, Any]], current_id: str | None) -> list[dict[str, Any]]:
    """Flag the entry matching ``current_id`` (aliases and labels included)."""
    if not current_id:
        return models
    norm = current_id.replace("-", "").lower()
    for m in models:
        mid_norm = str(m["id"]).replace("-", "").lower()
        label_norm = str(m.get("label", "")).replace("-", "").replace(" ", "").lower()
        if mid_norm == norm or label_norm == norm:
            m["current"] = True
            break
    return models


@dataclass
class ProfileYamlCatalog:
    """Read a layered-profile config (DSH): providers declare their models."""

    profile_home: Path | None = None

    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]:
        entries = dsh_profile.load_catalog(self.profile_home or ctx.profile_home)
        return [
            {
                "id": entry.model_id,
                "label": entry.label or entry.model_id,
                "vendor": entry.provider,
                **({"context": entry.context} if entry.context else {}),
                **({"custom": True} if entry.custom else {}),
            }
            for entry in entries
        ]


# ── model injection ──────────────────────────────────────────────────────────
class ModelInjector(Protocol):
    def argv(
        self, ctx: DialectContext, *, item_id: str, model: str
    ) -> list[str]: ...


@dataclass
class FlagInjector:
    """Most CLIs: pass the model as an argument to a dedicated flag."""

    flag: str = "--model"

    def argv(self, ctx: DialectContext, *, item_id: str, model: str) -> list[str]:
        return [self.flag, model]


@dataclass
class NoInjector:
    def argv(self, ctx: DialectContext, *, item_id: str, model: str) -> list[str]:
        return []


@dataclass
class DshPatchInjector:
    """Builtin escape hatch: DSH has no ``--model``; it takes a patch overlay.

    A per-run YAML file re-declares ``agent-default-model`` (plus the provider
    block when the headless profile does not know that gateway). Files are
    keyed by item id so concurrent runs cannot collide, and the executor
    deletes them when the run ends.
    """

    profile_home: Path | None = None

    def argv(self, ctx: DialectContext, *, item_id: str, model: str) -> list[str]:
        home = self.profile_home or ctx.profile_home
        declared: dict[str, dict[str, Any]] = {}
        catalog = dsh_profile.load_catalog(home, providers=declared)
        entry = dsh_profile.resolve_entry(catalog, model)
        if entry is None:
            # A model of a hand-declared route that its patch layer did not
            # list: rebuild the route from the declaration so the overlay
            # re-declares endpoint, credential and protocol. ``provider/<id>``
            # pins the route id (which may itself contain "/").
            route, _, bare = model.partition("/")
            if route in dsh_profile.BUILTIN_PROVIDERS:
                bare = ""
            if bare and route in declared:
                entry = dsh_profile.ModelEntry(
                    provider=route,
                    model_id=bare,
                    custom=True,
                    route_known=route in dsh_profile.BUILTIN_PROVIDERS,
                    **dsh_profile.provider_route(declared, route),
                )
            elif not bare and len(declared) == 1:
                (route,) = declared
                if route not in dsh_profile.BUILTIN_PROVIDERS:
                    entry = dsh_profile.ModelEntry(
                        provider=route,
                        model_id=model,
                        custom=True,
                        route_known=route in dsh_profile.BUILTIN_PROVIDERS,
                        **dsh_profile.provider_route(declared, route),
                    )
        pinned = dsh_profile.parse_pinned_target(
            ctx.run_cli(["--profile", "headless", "--dump-config"])[1]
        )
        provider = entry.provider if entry else (pinned[0] if pinned else "")
        if not provider:
            provider = "deepseek-official"
        text = dsh_profile.build_patch_yaml(
            provider=provider,
            model_id=entry.model_id if entry else model,
            entry=entry,
            default_provider=pinned[0] if pinned else "",
        )
        # Absolute: the CLI's cwd is the worktree, not the server's cwd.
        path = (ctx.patch_dir / f"{dsh_profile.safe_patch_name(item_id)}.yml").expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return ["--patch", str(path)]


# ── capability probes ────────────────────────────────────────────────────────
class CapabilityProbe(Protocol):
    def check(
        self, ctx: DialectContext, models: list[dict[str, Any]]
    ) -> bool: ...


@dataclass
class AlwaysProbe:
    value: bool = True

    def check(self, ctx: DialectContext, models: list[dict[str, Any]]) -> bool:
        return self.value


@dataclass
class HelpContainsProbe:
    needle: str = "--model"

    def check(self, ctx: DialectContext, models: list[dict[str, Any]]) -> bool:
        return self.needle in ctx.run_cli(["--help"])[1]


@dataclass
class ComposeVerifyProbe:
    """Builtin probe for overlay-based injection.

    Applying an overlay must change the composed default model; an overlay the
    CLI silently ignores would leave it unchanged, which is the silent-wrong-
    model failure we refuse to accept.
    """

    def check(self, ctx: DialectContext, models: list[dict[str, Any]]) -> bool:
        if "--patch" not in ctx.run_cli(["--help"])[1]:
            return False
        pinned = dsh_profile.parse_pinned_target(
            ctx.run_cli(["--profile", "headless", "--dump-config"])[1]
        )
        if pinned is None:
            return False
        candidate = next(
            (
                dsh_profile.resolve_entry(dsh_profile.load_catalog(ctx.profile_home), m["id"])
                for m in models
                if m.get("id") and m["id"] != pinned[1]
            ),
            None,
        )
        if candidate is None:
            return False  # nothing else to select => nothing to verify
        probe_dir = ctx.patch_dir / ".probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe_path = probe_dir / "capability.yml"
        probe_path.write_text(
            dsh_profile.build_patch_yaml(
                provider=candidate.provider,
                model_id=candidate.model_id,
                entry=candidate,
                default_provider=pinned[0],
            ),
            encoding="utf-8",
        )
        composed = ctx.run_cli(
            ["--profile", "headless", "--patch", str(probe_path), "--dump-config"]
        )[1]
        target = dsh_profile.parse_pinned_target(composed)
        return bool(target and target[1] == candidate.model_id)


# ── dialect bundle ───────────────────────────────────────────────────────────
@dataclass
class CliDialect:
    """The four strategies that define one CLI, composed not configured."""

    catalog: ModelCatalog
    injector: ModelInjector
    probe: CapabilityProbe
    verdict: StrictVerdict = field(default_factory=StrictVerdict)

    def discover(self, ctx: DialectContext) -> list[dict[str, Any]]:
        return self.catalog.discover(ctx)

    def inject(self, ctx: DialectContext, *, item_id: str, model: str) -> list[str]:
        return self.injector.argv(ctx, item_id=item_id, model=model)

    def capability(
        self, ctx: DialectContext, models: list[dict[str, Any]]
    ) -> bool:
        return self.probe.check(ctx, models)


def run_cli(
    command: str,
    env: dict[str, str],
    args: Sequence[str],
    timeout: float = 20.0,
) -> tuple[int, str, str]:
    """Run the CLI once for discovery/probing; never raises."""
    try:
        proc = subprocess.run(
            [command, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return -1, "", ""
    return proc.returncode, proc.stdout or "", proc.stderr or ""