"""Read DSH profile layers so the scheduler can offer real model choices.

``dsh --profile headless`` exposes no ``--model`` flag: the model is pinned in
the composed profile's ``agent-default-model`` entry. It does, however, accept
``--patch <file>`` — an extra overlay applied after the profile layer — so a
single run can be pointed at another model without touching the user's profile.

The catalog of what *can* be selected lives in the same profile layers, mainly
the ``llm-pi-ai`` plugin's ``providers.<provider>.models`` list under
``$DSH_HOME/profiles/<name>/cordis.patch.yml``. Those files are YAML, but only a
narrow subset is needed here (a list of maps holding scalars plus one nested
``providers`` map), so this module parses that subset directly instead of
pulling in a YAML dependency.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelEntry:
    """One selectable model and the provider it must run under.

    ``custom`` marks a model whose provider route pi-ai does not ship (a
    hand-declared gateway), i.e. one the user added rather than the bundled
    catalog; the UI uses it to tell built-in models from custom ones.
    """

    provider: str
    model_id: str
    label: str = ""
    base_url: str = ""
    api_key_env: str = ""
    context: int | None = None
    custom: bool = False


# Provider routes shipped by pi-ai's bundled catalog (``PROVIDER_DATA`` in
# ``@deepseek-ai/dsh-llm-pi-ai``). A route name outside this list cannot reuse
# a catalog provider and must declare ``api`` itself.
BUILTIN_PROVIDERS: frozenset[str] = frozenset({
    "amazon-bedrock", "ant-ling", "anthropic", "azure", "baseten", "cerebras",
    "cloudflare-ai-gateway", "cloudflare-workers-ai", "deepseek", "fireworks",
    "github-copilot", "google", "google-vertex", "groq", "huggingface",
    "kimi-coding", "meta", "minimax", "minimax-cn", "mistral", "moonshotai",
    "moonshotai-cn", "nvidia", "openai", "openai-codex", "opencode",
    "opencode-go", "openrouter", "qwen-token-plan", "qwen-token-plan-cn",
    "qwen-token-plan-individual", "radius", "together", "typesafe",
    "vercel-ai-gateway", "xai", "xiaomi", "xiaomi-token-plan-ams",
    "xiaomi-token-plan-cn", "xiaomi-token-plan-sgp", "zai", "zai-coding-cn",
})


def dsh_home() -> Path:
    """``$DSH_HOME`` or ``~/.dsh`` — where profiles and their patches live."""
    raw = os.environ.get("DSH_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".dsh"


# ── minimal YAML subset parser ────────────────────────────────────────────────
def _strip_comment(line: str) -> str:
    """Drop full-line comments and trailing ``#`` comments outside quotes."""
    if line.lstrip().startswith("#"):
        return ""
    out: list[str] = []
    quote: str | None = None
    prev = ""
    for char in line:
        if quote:
            out.append(char)
            if char == quote:
                quote = None
        elif char in ('"', "'") and prev in ("", " ", ":", "-"):
            quote = char
            out.append(char)
        elif char == "#" and prev in (" ", "\t"):
            break
        else:
            out.append(char)
        prev = char
    return "".join(out).rstrip()


def _scalar(raw: str) -> Any:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("", "null", "~"):
        return None
    return value


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    """Parse a dict or list block that starts at ``index`` with ``indent``."""
    if index >= len(lines):
        return None, index
    as_list = lines[index][1].startswith("- ")

    if as_list:
        items: list[Any] = []
        while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
            rest = lines[index][1][2:]
            item: dict[str, Any] = {}
            if ":" in rest:
                key, _, raw_value = rest.partition(":")
                key = key.strip()
                if raw_value.strip():
                    item[key] = _scalar(raw_value)
                    index += 1
                else:
                    index += 1
                    if index < len(lines) and lines[index][0] > indent:
                        child, index = _parse_block(lines, index, lines[index][0])
                        item[key] = child
                    else:
                        item[key] = None
                # remaining keys of the same list item
                if (
                    index < len(lines)
                    and lines[index][0] > indent
                    and not lines[index][1].startswith("- ")
                ):
                    more, index = _parse_block(lines, index, lines[index][0])
                    if isinstance(more, dict):
                        item.update(more)
                items.append(item)
            else:
                items.append(_scalar(rest))
                index += 1
        return items, index

    mapping: dict[str, Any] = {}
    while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
        key, _, raw_value = lines[index][1].partition(":")
        key = key.strip()
        if raw_value.strip():
            mapping[key] = _scalar(raw_value)
            index += 1
        else:
            index += 1
            if index < len(lines) and lines[index][0] > indent:
                child, index = _parse_block(lines, index, lines[index][0])
                mapping[key] = child
            else:
                mapping[key] = None
    return mapping, index


def parse_patch(text: str) -> list[dict[str, Any]]:
    """Parse a ``cordis.patch.yml`` file into a list of loader patch entries."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    if not lines:
        return []
    parsed, _ = _parse_block(lines, 0, lines[0][0])
    return parsed if isinstance(parsed, list) else []


# ── catalog ──────────────────────────────────────────────────────────────────
def load_catalog(
    home: Path | None = None, providers: dict[str, dict[str, Any]] | None = None
) -> list[ModelEntry]:
    """Collect selectable models declared across the profile patch layers.

    Later profiles (alphabetical) win on duplicate ``provider/model`` pairs, and
    a provider's ``baseURL`` / ``apiKeyEnv`` is remembered so a per-run patch can
    re-declare it when the headless profile does not have it.

    ``providers`` may receive every declared route (name → ``{base_url,
    api_key_env}``) so a per-run patch can re-declare a route whose models were
    not listed in the patch layers.
    """
    root = (home or dsh_home()) / "profiles"
    if not root.is_dir():
        return []

    declared_providers: dict[str, dict[str, Any]] = {}
    seen: dict[tuple[str, str], ModelEntry] = {}

    for patch_file in sorted(root.glob("*/cordis.patch.yml")):
        try:
            entries = parse_patch(patch_file.read_text(encoding="utf-8"))
        except OSError:
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id") != "llm-pi-ai":
                continue
            config = entry.get("config") or {}
            declared = config.get("providers") or {}
            if not isinstance(declared, dict):
                continue
            for name, provider_config in declared.items():
                if not isinstance(provider_config, dict):
                    continue
                slot = declared_providers.setdefault(name, {"base_url": "", "api_key_env": ""})
                if provider_config.get("baseURL"):
                    slot["base_url"] = str(provider_config["baseURL"])
                if provider_config.get("apiKeyEnv"):
                    slot["api_key_env"] = str(provider_config["apiKeyEnv"])
                for model in provider_config.get("models") or []:
                    if not isinstance(model, dict) or not model.get("id"):
                        continue
                    model_id = str(model["id"])
                    seen[(name, model_id)] = ModelEntry(
                        provider=name,
                        model_id=model_id,
                        label=str(model.get("name") or model_id),
                        base_url=slot["base_url"],
                        api_key_env=slot["api_key_env"],
                        context=_as_int(model.get("contextWindow")),
                        custom=name not in BUILTIN_PROVIDERS,
                    )

    if providers is not None:
        providers.update({name: dict(slot) for name, slot in declared_providers.items()})

    # fill provider connection details that may have been declared without models
    return [
        ModelEntry(
            provider=entry.provider,
            model_id=entry.model_id,
            label=entry.label,
            base_url=declared_providers.get(entry.provider, {}).get("base_url", entry.base_url),
            api_key_env=declared_providers.get(entry.provider, {}).get(
                "api_key_env", entry.api_key_env
            ),
            context=entry.context,
            custom=entry.custom,
        )
        for entry in seen.values()
    ]


def parse_pinned_target(dump_text: str) -> tuple[str, str] | None:
    """Extract ``(provider, model)`` from ``dsh --profile headless --dump-config``.

    The composed tree lists the ``agent-default-model`` entry with its config on
    following indented lines; the entry ends at the next ``- id:`` marker.
    """
    # The composer annotates patched entries with "# == … patched by <file>"
    # comment lines on both sides, so comments must be allowed inside the block;
    # otherwise the lookahead for the next "- id:" never matches.
    found = re.search(
        r"id:\s*agent-default-model.*?\n((?:(?:\s+.*|#.*)\n)*?)(?=- id:|\Z)", dump_text
    )
    if not found:
        return None
    block = found.group(1)
    provider = re.search(r"^\s+provider:\s*([A-Za-z0-9._-]+)", block, re.MULTILINE)
    model = re.search(r"^\s+model:\s*([A-Za-z0-9._-]+)", block, re.MULTILINE)
    if provider and model:
        return provider.group(1), model.group(1)
    return None


def _as_int(value: Any) -> int | None:
    """YAML scalars arrive as strings from the subset parser; normalise to int."""
    if value is None:
        return None
    try:
        return int(str(value).replace("_", ""))
    except (TypeError, ValueError):
        return None


def resolve_entry(catalog: list[ModelEntry], model_id: str) -> ModelEntry | None:
    """Find the catalog entry for a model id (plain id or ``provider/model``)."""
    provider, _, bare = model_id.partition("/")
    if bare:
        for entry in catalog:
            if entry.provider == provider and entry.model_id == bare:
                return entry
        return None
    for entry in catalog:
        if entry.model_id == model_id:
            return entry
    return None


def provider_route(providers: dict[str, dict[str, Any]], provider: str) -> dict[str, str]:
    """Model metadata for one route (for models discovered from its endpoint)."""
    slot = providers.get(provider) or {}
    return {
        "base_url": str(slot.get("base_url") or ""),
        "api_key_env": str(slot.get("api_key_env") or ""),
    }


def discovery_route(providers: dict[str, dict[str, Any]]) -> str:
    """Hand-declared route whose models the profile layers do not describe.

    These are the routes worth asking their endpoint ``GET /models``: pi-ai's
    bundled catalog does not know their models, so nothing else lists them. An
    explicit ``TASKAGENT_DSH_DISCOVERY_ROUTE`` wins, otherwise the first
    hand-declared route in profile order. Returns "" when every route is one
    the bundled catalog ships.
    """
    custom = [name for name in providers if name not in BUILTIN_PROVIDERS]
    wanted = os.environ.get("TASKAGENT_DSH_DISCOVERY_ROUTE", "").strip()
    if wanted:
        return wanted if wanted in custom else ""
    return custom[0] if custom else ""


def fetch_route_models(
    route: dict[str, str], env: dict[str, str], timeout: float = 8.0
) -> list[dict[str, Any]]:
    """``GET {baseURL}/models`` for a hand-declared route (OpenAI-compatible).

    Custom gateways are not described by pi-ai's bundled catalog, so the only
    place their models are listed is the endpoint itself. Best-effort: any
    failure (no URL, no credential, network error, odd payload) yields an empty
    list rather than an error the UI would have to handle.
    """
    base_url = (route.get("base_url") or "").rstrip("/")
    key_name = route.get("api_key_env") or ""
    if not base_url.startswith(("http://", "https://")):
        return []
    api_key = env.get(key_name, "") if key_name else ""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    import httpx

    try:
        response = httpx.get(f"{base_url}/models", headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        model_id = item["id"].strip()
        if not model_id:
            continue
        label = str(item.get("name") or "").strip() or model_id
        model: dict[str, Any] = {"id": model_id, "label": label}
        context = _as_int(item.get("context_window") or item.get("max_input_tokens"))
        if context:
            model["context"] = context
        models.append(model)
    return models


# Wire protocol a hand-declared (custom) pi-ai route speaks. Every route the
# bundled catalog does not ship must name one; OpenAI-compatible gateways are
# the norm for user-added endpoints.
CUSTOM_ROUTE_API = "openai-completions"


def build_patch_yaml(
    *,
    provider: str,
    model_id: str,
    entry: ModelEntry | None,
    default_provider: str,
) -> str:
    """Render a ``--patch`` overlay that points this run at ``model_id``.

    The provider block is only emitted when it is not already the headless
    profile's own provider; re-declaring an existing provider is harmless but
    noisier, and the patch file shows up in reports. A custom route (one pi-ai
    does not ship) additionally needs its own ``api`` — otherwise the overlay
    is refused and the task silently falls back to the default model.
    """
    lines: list[str] = []
    if entry is not None and entry.provider != default_provider:
        lines += [
            '- id: llm-pi-ai',
            '  name: "@deepseek-ai/dsh-llm-pi-ai"',
            '  config:',
            '    providers:',
            f'      {entry.provider}:',
        ]
        if entry.base_url:
            lines.append(f'        baseURL: {entry.base_url}')
        if entry.api_key_env:
            lines.append(f'        apiKeyEnv: {entry.api_key_env}')
        if entry.custom:
            lines.append(f"        api: {CUSTOM_ROUTE_API}")
        lines += [
            '        models:',
            f'          - id: {entry.model_id}',
            f'            name: {entry.label or entry.model_id}',
        ]
    lines += [
        '- id: agent-default-model',
        '  name: "@deepseek-ai/dsh-agent-default-model"',
        '  config:',
        f'    provider: {provider}',
        f'    model: {model_id}',
    ]
    return "\n".join(lines) + "\n"


def credential_refs(home: Path | None = None) -> dict[str, str]:
    """Env-style credentials stored by DSH's own credential service.

    The desktop app writes ``$DSH_HOME/.credentials.yaml``; its ``refs:`` entry
    maps provider api-key env names to secrets, in either block style::

        refs:
          {
            QWEN_TOKEN_PLAN_API_KEY: sk-...
          }

    or the list form ``refs:\n  - KEY: value``. The headless CLI only reads
    the *environment*, so a key configured in the app otherwise looks "not
    set". Values are returned for injection into the subprocess env and must
    never be logged.
    """
    path = (home or dsh_home()) / ".credentials.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    found = re.search(r"^refs:[ \t]*(.*?)$(.*?)(?=^[A-Za-z_][\w.-]*:|\Z)", text, re.MULTILINE | re.DOTALL)
    if not found:
        return {}
    head, body = found.group(1).strip(), found.group(2)
    block = head + "\n" + body if head else body

    refs: dict[str, str] = {}
    # flow map: { KEY: value, ... }  (possibly spread over lines)
    for key, value in re.findall(
        r"([A-Za-z_][\w.-]*)\s*:\s*([^\s,{}]+)", block
    ):
        refs[key] = value.strip("'\"")
    return refs


def patch_ignored_warning(stderr: str) -> str | None:
    """Detect the CLI's "patch entry not found" note.

    ``dsh`` exits 0 and merely warns on stderr when an overlay targets an id the
    profile does not have. In that case the task silently ran with the profile's
    default model, so the caller must not report success.
    """
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("dsh:") and "patch" in stripped and "not found" in stripped:
            return f"模型覆盖未被 DSH 接受（{stripped}），任务使用了 profile 默认模型"
    return None


def safe_patch_name(item_id: str) -> str:
    """File name for an item's patch overlay (ids are already tame; stay safe)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", item_id)[:120] or "item"