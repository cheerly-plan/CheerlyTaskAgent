"""把 TaskSpec 的验收标准编译为可执行断言，并以执行结果作为门禁依据。

设计约束（与需求一一对应）：

- **确定性**：编译只依赖 spec 内容，不读时间、随机数或 LLM 输出；同一输入两次
  生成的 ``render_cases()`` 文本字节级一致。
- **显式 unverified**：无法编译为确定性断言的条目不会被丢弃，而是带着条目 ID 和
  原因进入报告。默认策略下只要存在 unverified，门禁就不会输出 pass。
- **沙箱执行**：文件断言限定在 worktree 目录内（拒绝绝对路径/``..``/逃逸软链，
  并按文件类型/大小设限）；命令断言按“解析后的绝对路径 + 可信系统目录”校验白名单，
  拒绝相对路径/``~``/``..`` 参数、清洗环境变量（HOME 指向 worktree）、禁用 shell、
  独立进程组超时杀死并限制输出量；worktree 缺失时 fail-closed，绝不回落到 cwd。
- **失败隔离**：逐条 try/except + 子进程超时，单条失败/超时/崩溃不影响其余用例。
- **兼容**：本模块不改动 spec 既有字段语义，只在报告里新增字段；旧 spec（无结构化
  验收标准）走 unverified 路径，不抛异常。

本模块不做任何网络请求，也不调用 LLM。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .spec import TaskSpec
from .verification import _build_env, _redact, _secret_values

# 默认超时 <= 60s / 用例；可通过配置覆盖。
DEFAULT_TIMEOUT_S = 60.0
MIN_TIMEOUT_S = 0.2

# unverified 默认策略：存在不可验证条目时不得输出 pass。
DEFAULT_UNVERIFIED_POLICY = "needs_review"
UNVERIFIED_POLICIES = ("needs_review", "allow", "fail")

# 命令白名单。刻意不含 curl/wget/ssh/rm/sudo 等联网或破坏性工具。
DEFAULT_ALLOWED_COMMANDS: tuple[str, ...] = (
    "python", "python3", "pytest", "ruff", "true", "false", "echo", "cat",
    "grep", "test", "printf", "sleep", "head", "tail", "wc", "sort", "diff",
)
# 白名单只决定「允许的名字」；真正执行前还必须解析到可信目录下的绝对路径。
_SYSTEM_BIN_DIRS: tuple[Path, ...] = tuple(
    Path(item) for item in ("/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin")
)
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024

_FILE_KINDS = ("exists", "not_exists", "contains", "not_contains")
_COMMAND_KINDS = ("command_exit0", "output_contains")
_KINDS = _FILE_KINDS + _COMMAND_KINDS

_KIND_ALIASES = {
    "file_exists": "exists",
    "file_not_exists": "not_exists",
    "not_file_exists": "not_exists",
    "missing": "not_exists",
    "absent": "not_exists",
    "file_contains": "contains",
    "not_file_contains": "not_contains",
    "exit0": "command_exit0",
    "command": "command_exit0",
    "cmd": "command_exit0",
    "command_ok": "command_exit0",
}

# 支持形如 ``AC-001: xxx`` / ``[AC-001] xxx`` / ``AC001 xxx`` 的显式条目 ID。
_ID_PREFIX_RE = re.compile(
    r"^\s*(?:\[([^\]]+)\]|\b(AC[\s_-]*\d+)\b)\s*[:：.\-]?\s*", re.IGNORECASE
)


# ── 编译：spec -> 用例（纯函数，确定性）────────────────────────────────────────
@dataclass(frozen=True)
class AcceptanceItem:
    id: str
    text: str
    check: dict[str, Any] | None = None
    # 非空时强制以 unverified 入报告，避免重复 ID / 非法断言被静默丢弃。
    unverified_reason: str = ""


@dataclass(frozen=True)
class CompiledCase:
    id: str
    acceptance_id: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)
    spec_id: str = ""
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "acceptance_id": self.acceptance_id,
            "kind": self.kind,
            "params": dict(self.params),
            "spec_id": self.spec_id,
            "source_text": self.source_text,
        }


@dataclass(frozen=True)
class Compilation:
    spec_id: str
    cases: tuple[CompiledCase, ...]
    unverified: tuple[dict[str, str], ...]
    mapping: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "cases": [case.to_dict() for case in self.cases],
            "unverified": [dict(item) for item in self.unverified],
            "mapping": dict(self.mapping),
        }


def _spec_dict(spec: Any) -> dict[str, Any]:
    if isinstance(spec, TaskSpec):
        return spec.to_dict()
    if isinstance(spec, dict):
        return spec
    return {}


def _split_id(text: str, explicit: str, index: int, id_list: list[str]) -> tuple[str, str]:
    """拆出显式验收条目 ID，返回 (id, 剩余断言文本)。"""
    value = str(text or "").strip()
    match = _ID_PREFIX_RE.match(value)
    parsed = ""
    if match:
        parsed = str(match.group(1) or match.group(2) or "").strip()
        value = value[match.end():].strip()
    fallback = ""
    if index - 1 < len(id_list):
        fallback = id_list[index - 1].strip()
    identifier = (str(explicit or "").strip() or parsed or fallback or f"AC-{index:03d}")
    return identifier[:120], value


def _normalise_check(raw: Any) -> dict[str, Any] | None:
    """校验/规整结构化断言；无法识别时返回 None（调用方标记 unverified）。"""
    if not isinstance(raw, dict):
        return None
    kind = str(
        raw.get("type") or raw.get("kind") or raw.get("assertion") or ""
    ).strip().lower()
    kind = _KIND_ALIASES.get(kind, kind)
    if kind not in _KINDS:
        return None
    if kind in _FILE_KINDS:
        path = str(raw.get("path") or raw.get("target") or "").strip()
        if not path:
            return None
        params: dict[str, Any] = {"path": path}
        if kind in ("contains", "not_contains"):
            expected = raw.get("expected", raw.get("value", ""))
            if expected is None or not str(expected):
                return None
            params["expected"] = str(expected)
        return {"kind": kind, **params}
    command = raw.get("command") if "command" in raw else raw.get("argv")
    if isinstance(command, (list, tuple)):
        argv = [str(part) for part in command if str(part).strip()]
    else:
        try:
            argv = shlex.split(str(command or ""))
        except ValueError:
            return None
    if not argv:
        return None
    params = {"argv": argv}
    if kind == "output_contains":
        expected = raw.get("expected", raw.get("value", ""))
        if expected is None or not str(expected):
            return None
        params["expected"] = str(expected)
    return {"kind": kind, **params}


def _check_from_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    for key in ("assertion", "check"):
        if key in entry:
            value = entry[key]
            if isinstance(value, str):
                return _parse_check(value)
            return _normalise_check(value)
    kind = str(entry.get("type") or "").strip().lower()
    if kind and any(key in entry for key in ("path", "target", "command", "argv")):
        return _normalise_check(entry)
    return None


def _parse_check(text: str) -> dict[str, Any] | None:
    """把文本验收标准解析为断言；不认识的文本返回 None -> unverified。"""
    value = " ".join(str(text or "").split())
    if not value:
        return None

    match = re.match(r"(?i)^(?:exists|file_exists|文件存在)\s+(.+)$", value)
    if match:
        return _normalise_check({"type": "exists", "path": match.group(1)})

    match = re.match(r"(?i)^(?:not_exists|missing|absent|文件不存在)\s+(.+)$", value)
    if match:
        return _normalise_check({"type": "not_exists", "path": match.group(1)})

    match = re.match(
        r"(?i)^(?:not_contains|不包含)\s+(\S+)\s+(.+)$", value
    )
    if match:
        return _normalise_check({
            "type": "not_contains", "path": match.group(1), "expected": match.group(2),
        })

    match = re.match(r"(?i)^(?:contains|file_contains|包含)\s+(\S+)\s+(.+)$", value)
    if match:
        return _normalise_check({
            "type": "contains", "path": match.group(1), "expected": match.group(2),
        })

    match = re.match(r"(?i)^(?:output_contains|输出包含)\s+(.+?)\s*=>\s*(.+)$", value)
    if match:
        return _normalise_check({
            "type": "output_contains", "command": match.group(1),
            "expected": match.group(2),
        })

    match = re.match(r"(?i)^(?:exit0|command|cmd|命令成功)\s+(.+)$", value)
    if match:
        return _normalise_check({"type": "command_exit0", "command": match.group(1)})
    return None


def _acceptance_items(data: dict[str, Any]) -> list[AcceptanceItem]:
    raw = data.get("acceptance") or []
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[\n；;]+", raw) if part.strip()]
    if not isinstance(raw, list):
        raw = []
    ids_raw = data.get("acceptance_ids")
    id_list = [str(item).strip() for item in ids_raw] if isinstance(ids_raw, list) else []

    items: list[AcceptanceItem] = []
    seen: set[str] = set()

    def add(identifier: str, text: str, check: dict[str, Any] | None,
            reason: str = "") -> None:
        # 重复 ID / 非法断言必须显式落为 unverified，绝不静默丢弃。
        if identifier in seen:
            reason = "重复的验收条目 ID，无法与用例一一对应"
        else:
            seen.add(identifier)
        items.append(AcceptanceItem(identifier, text, check, reason))

    for index, entry in enumerate(raw, 1):
        if isinstance(entry, dict):
            explicit = str(
                entry.get("id") or entry.get("acceptance_id") or ""
            ).strip()
            text = str(
                entry.get("text") or entry.get("description") or entry.get("summary") or ""
            )
            check = _check_from_entry(entry)
        else:
            explicit, text, check = "", str(entry), None
        identifier, text = _split_id(text, explicit, index, id_list)
        add(identifier, text, check)

    # spec 可以额外给出结构化断言列表（同样带显式 ID），与 acceptance 合并。
    extra = data.get("assertions")
    if isinstance(extra, list):
        for index, entry in enumerate(extra, 1):
            explicit = ""
            text = ""
            check: dict[str, Any] | None = None
            reason = ""
            if isinstance(entry, dict):
                explicit = str(entry.get("id") or entry.get("acceptance_id") or "").strip()
                text = str(entry.get("text") or entry.get("description") or "")
                check = _normalise_check(entry)
                if check is None:
                    reason = "断言定义无法编译为确定性检查"
            else:
                text = str(entry)
                reason = "断言条目不是结构化对象"
            position = len(items) + index
            identifier, text = _split_id(text, explicit, position, id_list)
            add(identifier, text, check, reason)
    return items


def _make_spec_id(data: dict[str, Any], items: list[AcceptanceItem]) -> str:
    explicit = str(data.get("spec_id") or data.get("id") or "").strip()
    if explicit:
        return explicit[:200]
    canonical = json.dumps(
        [item.text for item in items], ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    )
    return "spec-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def compile_spec(spec: Any, *, spec_id: str = "") -> Compilation:
    """把 spec 编译为确定性用例；不可编译的条目显式写入 ``unverified``。"""
    data = _spec_dict(spec)
    items = _acceptance_items(data)
    sid = str(spec_id or "").strip() or _make_spec_id(data, items)

    cases: list[CompiledCase] = []
    unverified: list[dict[str, str]] = []
    mapping: dict[str, str] = {}
    for item in items:
        if item.unverified_reason:
            unverified.append({
                "acceptance_id": item.id,
                "status": "unverified",
                "reason": item.unverified_reason,
                "text": item.text[:500],
            })
            continue
        check = item.check or _parse_check(item.text)
        if check is None:
            unverified.append({
                "acceptance_id": item.id,
                "status": "unverified",
                "reason": "无法编译为确定性断言（非结构化验收标准）",
                "text": item.text[:500],
            })
            continue
        case_id = f"case-{item.id}"
        cases.append(CompiledCase(
            id=case_id,
            acceptance_id=item.id,
            kind=str(check["kind"]),
            params=dict(check),
            spec_id=sid,
            source_text=item.text[:500],
        ))
        mapping[item.id] = case_id
    return Compilation(
        spec_id=sid,
        cases=tuple(cases),
        unverified=tuple(unverified),
        mapping=mapping,
    )


# 常用别名，便于直接以“生成用例”语义调用。
compile_cases = compile_spec
generate_cases = compile_spec


def render_cases(compilation: Compilation) -> str:
    """确定性序列化：不含时间戳/随机数，同一输入字节级一致。"""
    return json.dumps(
        compilation.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"


# ── 执行：沙箱 + 超时 + 失败隔离 ───────────────────────────────────────────────
def _safe_path(root: Path, rel: str) -> Path | None:
    """把相对路径限制在 root 内；绝对路径/``..``/逃逸软链一律拒绝。"""
    text = str(rel or "").strip()
    if not text or Path(text).is_absolute():
        return None
    try:
        resolved_root = root.resolve()
        target = (root / text).resolve()
        target.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    return target


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory.resolve())
        return True
    except (OSError, ValueError):
        return False


def _trusted_executable(argv0: str, allowed: list[str], root: Path) -> str | None:
    """把白名单名字解析为可信目录下的绝对路径；否则拒绝执行。"""
    name = Path(argv0).name
    if name not in allowed:
        return None
    raw = str(argv0)
    if raw.startswith("~"):
        return None
    base = Path(raw)
    if not base.is_absolute() and (os.sep in raw or (os.altsep and os.altsep in raw)):
        # ./python、../bin/python 这类相对路径一律不信任。
        return None
    candidate = base if base.is_absolute() else Path(shutil.which(name) or "")
    if not str(candidate):
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or _within(resolved, root):
        return None
    trusted_dirs = list(_SYSTEM_BIN_DIRS) + [Path(sys.executable).resolve().parent]
    if not any(_within(resolved, directory) for directory in trusted_dirs):
        return None
    return str(resolved)


def _unsafe_arg(arg: Any) -> bool:
    """拒绝指向 worktree 之外的路径参数（绝对路径 / ~ / ..）。"""
    text = str(arg)
    if not text:
        return False
    if text.startswith("~") or Path(text).is_absolute():
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    return ".." in re.split(r"[\\/]+", text)


def _communicate_capped(
    proc: subprocess.Popen[str], timeout_s: float
) -> tuple[str, bool, bool]:
    """返回 (输出, 是否截断, 是否超时)；增量读取，避免输出撑爆内存。"""
    box: dict[str, Any] = {"text": "", "truncated": False}

    def reader() -> None:
        chunks: list[str] = []
        total = 0
        stream = proc.stdout
        if stream is None:
            return
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            remaining = _MAX_OUTPUT_BYTES - total
            if remaining > 0:
                piece = chunk[:remaining]
                chunks.append(piece)
                total += len(piece)
            if total >= _MAX_OUTPUT_BYTES:
                box["truncated"] = True
        box["text"] = "".join(chunks)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    timed_out = False
    try:
        proc.wait(timeout=max(MIN_TIMEOUT_S, timeout_s))
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        timed_out = True
    thread.join(timeout=5)
    return str(box["text"]), bool(box["truncated"]), timed_out


def _run_one(
    case: CompiledCase,
    root: Path,
    timeout_s: float,
    allowed_commands: list[str],
    env_extra: dict[str, Any] | None,
    secrets: list[str],
) -> dict[str, Any]:
    started = time.monotonic()
    kind = case.kind
    if kind in _FILE_KINDS:
        target = _safe_path(root, str(case.params.get("path") or ""))
        if target is None:
            return {"status": "error", "error": "路径非法或越出 worktree", "exit_code": None}
        try:
            if kind in ("exists", "not_exists"):
                exists = target.exists()
                ok = exists if kind == "exists" else not exists
            else:
                # is_file() 只认普通文件：FIFO / 设备文件不会阻塞读取。
                if not target.is_file():
                    ok = kind == "not_contains"
                else:
                    info = target.stat()
                    if info.st_size > _MAX_FILE_BYTES:
                        return {
                            "status": "error", "error": "文件超过大小上限，拒绝读取",
                            "exit_code": None,
                        }
                    found = str(case.params.get("expected") or "") in target.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    ok = found if kind == "contains" else not found
        except (OSError, ValueError) as error:
            return {"status": "error", "error": f"{type(error).__name__}: {error}"[:500],
                    "exit_code": None}
        if time.monotonic() - started > max(MIN_TIMEOUT_S, timeout_s):
            return {"status": "timeout", "error": f"用例超时（{timeout_s:g}s）",
                    "exit_code": None}
        if ok:
            return {"status": "pass", "error": "", "exit_code": None}
        return {
            "status": "fail",
            "error": f"断言不满足：{kind} {case.params.get('path') or ''}"[:500],
            "exit_code": None,
        }

    argv = list(case.params.get("argv") or [])
    if not argv:
        return {"status": "error", "error": "命令为空", "exit_code": None}
    executable = _trusted_executable(str(argv[0]), allowed_commands, root)
    if executable is None:
        return {
            "status": "error",
            "error": f"命令不在可信白名单：{Path(str(argv[0])).name}"[:500],
            "exit_code": None,
        }
    if any(_unsafe_arg(part) for part in argv[1:]):
        return {
            "status": "error",
            "error": "命令参数含越界路径（绝对路径 / ~ / ..）",
            "exit_code": None,
        }
    argv = [executable] + [str(part) for part in argv[1:]]

    env = _build_env(env_extra or {})
    # 不把真实用户目录/凭据位置暴露给子进程。
    env["HOME"] = str(root)
    env["XDG_CONFIG_HOME"] = str(root / ".config")
    env["XDG_CACHE_HOME"] = str(root / ".cache")
    # 尽力而为的网络隔离：切断常见 HTTP 客户端的外网出口，仅保留本地回环。
    # （跨平台没有统一的 OS 级沙箱，凭据已从 env 白名单中剔除。）
    blocked_proxy = "http://127.0.0.1:1"
    loopback = "localhost,127.0.0.1,::1"
    env.update({
        "HTTP_PROXY": blocked_proxy, "HTTPS_PROXY": blocked_proxy,
        "ALL_PROXY": blocked_proxy, "http_proxy": blocked_proxy,
        "https_proxy": blocked_proxy, "all_proxy": blocked_proxy,
        "NO_PROXY": loopback, "no_proxy": loopback,
    })
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        return {
            "status": "error",
            "error": f"{type(error).__name__}: {error}"[:500],
            "exit_code": None,
        }

    stdout, truncated, timed_out = _communicate_capped(proc, timeout_s)
    if timed_out:
        return {
            "status": "timeout",
            "error": f"用例超时（{timeout_s:g}s）",
            "exit_code": None,
            "tail": _redact(stdout, secrets)[-2000:],
        }

    exit_code = proc.returncode
    output = _redact(stdout, secrets)
    if truncated:
        output += f"\n[输出已截断，上限 {_MAX_OUTPUT_BYTES} 字节]"
    if exit_code is None or exit_code < 0:
        status = "error"
        error = f"进程异常终止（退出码 {exit_code}）"
    elif exit_code != 0:
        status = "fail"
        error = f"命令退出码 {exit_code}"
    else:
        status = "pass"
        error = ""
    expected = str(case.params.get("expected") or "")
    if status == "pass" and kind == "output_contains" and expected not in output:
        status = "fail"
        error = "输出未包含期望文本"
    return {
        "status": status,
        "error": error[:500],
        "exit_code": exit_code,
        "tail": output[-2000:],
    }


def run_cases(
    cases: Any,
    *,
    worktree: str | Path | None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    allowed_commands: Any = None,
    env_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """逐条执行用例；单条超时/异常/崩溃不影响其余用例。"""
    root = Path(worktree).expanduser() if str(worktree or "").strip() else None
    if root is None or not root.is_dir():
        # fail-closed：worktree 缺失时绝不在 cwd / 真实仓库里执行用例。
        return [
            {
                "id": getattr(case, "id", ""),
                "acceptance_id": getattr(case, "acceptance_id", ""),
                "status": "error",
                "duration": 0.0,
                "error": "worktree 不存在或未提供，拒绝执行（沙箱 fail-closed）",
                "exit_code": None,
                "tail": "",
            }
            for case in (cases or ())
        ]
    root = root.resolve()
    commands = (
        [str(item) for item in allowed_commands if str(item).strip()]
        if isinstance(allowed_commands, (list, tuple)) and allowed_commands
        else list(DEFAULT_ALLOWED_COMMANDS)
    )
    try:
        timeout = float(timeout_s)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT_S
    secrets = _secret_values()
    results: list[dict[str, Any]] = []
    for case in cases or ():
        started = time.monotonic()
        row: dict[str, Any] = {
            "id": getattr(case, "id", ""),
            "acceptance_id": getattr(case, "acceptance_id", ""),
            "status": "error",
            "duration": 0.0,
            "error": "",
            "exit_code": None,
            "tail": "",
        }
        try:
            row.update(_run_one(case, root, timeout, commands, env_extra, secrets))
        except Exception as error:  # noqa: BLE001 - 失败隔离：继续跑完其余用例
            row["status"] = "error"
            row["error"] = f"{type(error).__name__}: {error}"[:500]
        row["duration"] = round(time.monotonic() - started, 3)
        results.append(row)
    return results


# 常用别名。
run_assertions = run_cases
execute_cases = run_cases


# ── 门禁判定 ───────────────────────────────────────────────────────────────────
def assertion_options(raw: Any) -> dict[str, Any]:
    """规整断言门禁配置；非法值回落到安全默认值。"""
    config = dict(raw) if isinstance(raw, dict) else {}
    policy = str(config.get("unverified_policy") or DEFAULT_UNVERIFIED_POLICY).strip().lower()
    if policy not in UNVERIFIED_POLICIES:
        policy = DEFAULT_UNVERIFIED_POLICY
    try:
        timeout = float(config.get("timeout_s") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_S
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT_S
    return {
        "enabled": bool(config.get("enabled", False)),
        "unverified_policy": policy,
        "timeout_s": timeout,
        "allowed_commands": config.get("allowed_commands"),
        "spec_id": str(config.get("spec_id") or "").strip(),
    }


def _summary(
    rows: list[dict[str, Any]], unverified: tuple[dict[str, str], ...]
) -> dict[str, int]:
    counts = {
        "total": len(rows), "pass": 0, "fail": 0,
        "error": 0, "timeout": 0, "unverified": len(unverified),
    }
    for row in rows:
        status = str(row.get("status") or "error")
        if status in counts:
            counts[status] += 1
    return counts


def _decide(
    summary: dict[str, int], policy: str, base_ok: bool
) -> tuple[bool, str, str, str]:
    """返回 (ok, verdict, state, decision_source)。判定只看断言与策略，不看 LLM。"""
    bad = summary["fail"] + summary["error"] + summary["timeout"]
    if bad:
        return False, "fail", "assertion_failed", "assertion"
    if summary["total"] == 0:
        return False, "needs_review", "needs_review", "unverified"
    if summary["unverified"]:
        if policy == "fail":
            return False, "fail", "assertion_unverified", "unverified"
        return False, "needs_review", "needs_review", "unverified"
    if not base_ok:
        return False, "fail", "failed", "assertion"
    return True, "pass", "passed", "assertion"


def _assertion_report(
    compilation: Compilation,
    results: list[dict[str, Any]],
    options: dict[str, Any],
) -> dict[str, Any]:
    by_id = {str(row.get("id")): row for row in results}
    rows: list[dict[str, Any]] = []
    for case in compilation.cases:
        raw = by_id.get(case.id, {})
        rows.append({
            "id": case.id,
            "acceptance_id": case.acceptance_id,
            "status": str(raw.get("status") or "error"),
            "duration": float(raw.get("duration") or 0.0),
            "error": str(raw.get("error") or ""),
            "exit_code": raw.get("exit_code"),
            "tail": str(raw.get("tail") or ""),
        })
    summary = _summary(rows, compilation.unverified)
    return {
        "enabled": True,
        "legacy_mode": False,
        "spec_id": compilation.spec_id,
        "default_unverified_policy": DEFAULT_UNVERIFIED_POLICY,
        "unverified_policy": options["unverified_policy"],
        "timeout_s": options["timeout_s"],
        "mapping": dict(compilation.mapping),
        "cases": rows,
        "unverified": [dict(item) for item in compilation.unverified],
        "summary": summary,
    }


def _finish(
    target: dict[str, Any],
    report: dict[str, Any],
    *,
    base_ok: bool,
    base_state: str,
    base_reason: str,
) -> dict[str, Any]:
    summary = report["summary"]
    ok, verdict, state, source = _decide(
        summary, str(report["unverified_policy"]), base_ok
    )
    if verdict == "fail" and summary["fail"] + summary["error"] + summary["timeout"]:
        decision_reason = (
            f"验收断言未通过：{summary['fail']} 失败 / {summary['error']} 错误 / "
            f"{summary['timeout']} 超时"
        )
    elif verdict == "needs_review":
        count = summary["unverified"] or summary["total"]
        decision_reason = (
            f"存在 {count} 条无法编译或未执行的验收标准，需人工复核"
            if summary["unverified"] else "没有可执行的验收标准，需人工复核"
        )
    elif verdict == "pass":
        decision_reason = f"验收断言全部通过（{summary['total']} 条）"
    else:
        decision_reason = base_reason

    outcome = dict(target)
    outcome["ok"] = ok
    outcome["verdict"] = verdict
    outcome["decision_source"] = source
    outcome["spec_id"] = report["spec_id"]
    outcome["assertions"] = report
    # 新判定写入新增字段；既有门禁的 state/reason 语义不改写（例如
    # passed_with_warnings / incomplete / unconfigured 的下游分支）。
    outcome["assertion_state"] = state
    outcome["assertion_reason"] = decision_reason
    if "state" not in target:
        outcome["state"] = state
        outcome["reason"] = decision_reason
    # 保留原始门禁判定，便于审计“默认行为变更”。
    outcome["legacy_verdict"] = {
        "ok": bool(base_ok), "state": base_state, "reason": base_reason[:500],
    }
    return outcome


def run_assertion_gate(
    spec: Any,
    *,
    worktree: str | Path,
    assertions: Any = None,
    spec_id: str = "",
) -> dict[str, Any]:
    """独立门禁：仅以 spec 用例执行结果判定，不依赖仓库检查、不调用 LLM。"""
    options = assertion_options(assertions)
    options["enabled"] = True
    options["spec_id"] = str(spec_id or options["spec_id"])
    compilation = compile_spec(spec, spec_id=options["spec_id"])
    results = run_cases(
        compilation.cases,
        worktree=worktree,
        timeout_s=options["timeout_s"],
        allowed_commands=options.get("allowed_commands"),
    )
    report = _assertion_report(compilation, results, options)
    return _finish(
        {}, report, base_ok=True, base_state="passed", base_reason=""
    )


def apply_assertion_gate(
    gate: dict[str, Any],
    *,
    spec: Any,
    worktree: str | Path | None = None,
    assertions: Any = None,
) -> dict[str, Any]:
    """把断言判定并入既有门禁；原有字段保留，新增 verdict/decision_source 等。"""
    options = assertion_options(assertions)
    compilation = compile_spec(spec, spec_id=options["spec_id"])
    results = run_cases(
        compilation.cases,
        worktree=worktree,
        timeout_s=options["timeout_s"],
        allowed_commands=options.get("allowed_commands"),
    )
    report = _assertion_report(compilation, results, options)
    return _finish(
        gate,
        report,
        base_ok=bool(gate.get("ok", True)),
        base_state=str(gate.get("state") or ""),
        base_reason=str(gate.get("reason") or ""),
    )


def gate_exit_code(gate: dict[str, Any]) -> int:
    """机器可读退出码约定：0=通过，非 0=不通过。"""
    return 0 if gate.get("ok") is True else 1


def write_report(gate: dict[str, Any], path: str | Path) -> str:
    """落盘机器可读 JSON 报告（键排序，便于比对）。"""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    target.write_text(text, encoding="utf-8")
    return str(target)
