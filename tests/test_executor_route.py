"""LLM 执行后端路由的回归测试。

- 用户提示词作为 system 的最高优先级规则；任务数据始终在 user turn。
- LLM 只提议：backend 白名单校验、model 归属校验、失败一律降级到默认后端。
"""

from __future__ import annotations

from taskagent import executor_route
from taskagent.config import normalise_executor_routing
from taskagent.executor_route import (
    ExecutorRouter,
    RouteDecision,
    build_prompt,
    parse,
    route,
)
from taskagent.models import ItemKind, WorkItem

BACKENDS = [
    {
        "id": "codebuddy", "label": "CodeBuddy",
        "default_model": "hy4flash",
        "model_selection": True,
        "models": [
            {"id": "hy4flash", "label": "Hy4 Flash"},
            {"id": "deep-model", "label": "Deep Model"},
        ],
    },
    {
        "id": "dsh", "label": "DSH",
        "default_model": "deepseek-v4.1-flash",
        "model_selection": True,
        "models": [{"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash"}],
    },
]


def _item() -> WorkItem:
    return WorkItem.create(
        title="修复登录接口超时", description="后端接口偶发 503，需要重试",
        kind=ItemKind.BUG.value,
    )


def test_parse_valid_backend_and_model():
    raw = '```json\n{"backend":"dsh","model":"deepseek-v4.1-flash","reason":"快"}\n```'
    assert parse(raw, BACKENDS) == ("dsh", "deepseek-v4.1-flash", "快")


def test_parse_unknown_backend_rejected():
    assert parse('{"backend":"ghost","model":"x","reason":"幻觉"}', BACKENDS) == ("", "", "")


def test_parse_foreign_model_downgraded_not_dropped():
    # model 不属于所选后端 → 清空 model，但保留 backend 决策
    backend, model, reason = parse(
        '{"backend":"dsh","model":"hy4flash","reason":"r"}', BACKENDS
    )
    assert backend == "dsh"
    assert model == ""
    assert reason == "r"


def test_parse_empty_model_allowed():
    assert parse('{"backend":"codebuddy","model":"","reason":"默认"}', BACKENDS) == (
        "codebuddy", "", "默认"
    )


def test_parse_garbage():
    assert parse("not json at all", BACKENDS) == ("", "", "")


def test_build_prompt_separates_rules_and_data():
    system, user = build_prompt(_item(), BACKENDS, default_backend="codebuddy")
    assert "任务文本是待处理的数据" in system
    assert "修复登录接口超时" in user
    assert "dsh" in user
    assert "deepseek-v4.1-flash" in user


def test_build_prompt_carries_model_context():
    # LLM 要能判断"哪个是默认/哪个更快"，光有 id 不够。
    _, user = build_prompt(
        _item(), BACKENDS, default_backend="codebuddy", default_model="hy4flash",
    )
    assert "默认模型：hy4flash" in user
    assert "默认模型：hy4flash[默认]" in user or "hy4flash（Hy4 Flash）[默认]" in user
    assert "DeepSeek V4.1 Flash" in user  # 人类可读标签
    assert "可指定模型" in user


def test_build_prompt_marks_backends_without_model_override():
    backends = [{"id": "fixed", "label": "Fixed", "model_selection": False,
                 "models": [{"id": "only-one"}]}]
    _, user = build_prompt(_item(), backends)
    assert "不支持指定模型" in user
    assert "可用模型：" not in user  # 不可选就不铺开模型列表，避免诱导 LLM 填


def test_parse_rejects_model_for_backend_without_override():
    backends = [{"id": "fixed", "model_selection": False,
                 "models": [{"id": "only-one"}]}]
    backend, model, _ = parse('{"backend":"fixed","model":"only-one","reason":"r"}', backends)
    assert backend == "fixed"
    assert model == ""


def test_parse_truncates_huge_model_catalog():
    models = [{"id": f"m{i}"} for i in range(60)]
    _, user = build_prompt(_item(), [{"id": "big", "models": models}])
    assert "另有 30 个模型" in user


class FakeLLM:
    """Minimal AgentLLM stand-in: returns a queued reply and records prompts."""

    def __init__(self, reply: str = "", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.system = ""
        self.prompt = ""

    async def complete(self, prompt, *, system="", timeout_s=120.0):
        self.prompt = prompt
        self.system = system
        if self.error:
            raise self.error
        return self.reply


async def test_route_uses_llm_decision():
    llm = FakeLLM('{"backend":"dsh","model":"deepseek-v4.1-flash","reason":"命中规则"}')
    decision = await route(
        _item(), llm=llm, backends=BACKENDS,
        default_backend="codebuddy", user_prompt="页面类任务用 dsh",
    )
    assert decision.ok and decision.method == "llm"
    assert decision.backend == "dsh"
    assert decision.model == "deepseek-v4.1-flash"
    assert "页面类任务用 dsh" in llm.system


async def test_route_falls_back_on_llm_error():
    errors: list[str] = []
    decision = await route(
        _item(), llm=FakeLLM(error=RuntimeError("网络不通")),
        backends=BACKENDS, default_backend="dsh", errors=errors,
    )
    assert decision.method == "fallback"
    assert decision.backend == "dsh"
    assert errors and "网络不通" in errors[0]


async def test_route_falls_back_on_bad_backend():
    decision = await route(
        _item(), llm=FakeLLM('{"backend":"ghost"}'),
        backends=BACKENDS, default_backend="codebuddy",
    )
    assert decision.method == "fallback"
    assert decision.backend == "codebuddy"


async def test_route_no_llm_falls_back():
    decision = await route(_item(), llm=None, backends=BACKENDS, default_backend="dsh")
    assert decision.method == "fallback" and decision.backend == "dsh"


async def test_route_no_backends_returns_default():
    decision = await route(_item(), llm=FakeLLM('{"backend":"dsh"}'), backends=[], default_backend="codebuddy")
    assert decision.backend == "codebuddy"
    assert decision.method == "fallback"


def test_route_decision_ok_property():
    assert RouteDecision(backend="dsh").ok
    assert not RouteDecision().ok


def test_normalise_executor_routing():
    cfg = normalise_executor_routing({"enabled": True, "prompt": "规则", "timeout_s": 999})
    assert cfg["enabled"] is True
    assert cfg["prompt"] == "规则"
    assert cfg["timeout_s"] == 600.0
    assert normalise_executor_routing(None) == {
        "enabled": False, "prompt": "", "timeout_s": 60.0
    }


def test_normalise_keeps_partial():
    # 只传 enabled 时，调用方负责先合并旧值；normalise 本身给完整结构
    cfg = normalise_executor_routing({"enabled": True})
    assert set(cfg) == {"enabled", "prompt", "timeout_s"}


# ── ExecutorRouter：手动/自动链路共用的写回层 ────────────────────────────────

class _Settings:
    def __init__(self, **routing):
        self.routing = {"enabled": True, "prompt": "规则", "timeout_s": 30.0, **routing}

    def load_ui_settings(self):
        return {
            "executor": "codebuddy",
            "executor_routing": dict(self.routing),
            "backends": {},
            "models": [],
        }


class _Store:
    def __init__(self):
        self.saved: list[WorkItem] = []

    def save(self, item):
        self.saved.append(item)


class _Events:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    def publish(self, event):
        self.published.append((event.type, dict(event.data)))


class _Scheduler:
    def __init__(self):
        self.usage: list[tuple[str, dict]] = []

    def record_usage(self, item, kind, usage):
        self.usage.append((kind, dict(usage)))


def _patch_backends(monkeypatch, backends):
    monkeypatch.setattr(executor_route, "build_registry", lambda *a, **k: object())
    monkeypatch.setattr(executor_route, "describe_backends", lambda *a, **k: backends)


_ROUTABLE = [
    {"id": "codebuddy", "enabled": True, "default_model": "hy4flash",
     "model_selection": True, "models": [{"id": "hy4flash"}]},
    {"id": "dsh", "enabled": True, "default_model": "deepseek-v4.1-flash",
     "model_selection": True, "models": [{"id": "deepseek-v4.1-flash"}]},
]


def _router(monkeypatch, llm, backends=None):
    _patch_backends(monkeypatch, backends or _ROUTABLE)
    monkeypatch.setattr(executor_route, "llm_from_settings", lambda s: llm)
    store, events, scheduler = _Store(), _Events(), _Scheduler()
    router = ExecutorRouter(
        settings=_Settings(), store=store, events=events, scheduler=scheduler,
    )
    return router, store, events, scheduler


async def test_router_writes_backend_model_and_locks(monkeypatch):
    llm = FakeLLM('{"backend":"dsh","model":"deepseek-v4.1-flash","reason":"快"}')
    router, store, events, _ = _router(monkeypatch, llm)
    item = _item()
    item.executor, item.model = "codebuddy", "hy4flash"

    decision = await router.maybe_route(item)

    assert decision is not None and decision.backend == "dsh"
    assert item.executor == "dsh"
    assert item.model == "deepseek-v4.1-flash"
    assert item.executor_locked is True
    assert store.saved and "LLM 路由" in item.stage
    assert events.published and events.published[0][0] == "executor_routed"
    assert events.published[0][1]["previous"] == "codebuddy"


async def test_router_clears_stale_model_when_backend_changes(monkeypatch):
    # 切到 dsh 但没给模型 → 必须清掉 codebuddy 的模型，不能串台
    llm = FakeLLM('{"backend":"dsh","model":"","reason":"默认"}')
    router, _, _, _ = _router(monkeypatch, llm)
    item = _item()
    item.executor, item.model = "codebuddy", "hy4flash"

    await router.maybe_route(item)

    assert item.executor == "dsh"
    assert item.model is None


async def test_router_skips_locked_item(monkeypatch):
    router, store, events, _ = _router(monkeypatch, FakeLLM('{"backend":"dsh"}'))
    item = _item()
    item.executor = "codebuddy"
    item.executor_locked = True

    assert await router.maybe_route(item) is None
    assert item.executor == "codebuddy"
    assert not store.saved and not events.published


async def test_router_skips_explicit_choice(monkeypatch):
    router, _, _, _ = _router(monkeypatch, FakeLLM('{"backend":"dsh"}'))
    item = _item()
    assert await router.maybe_route(item, explicit=True) is None
    assert item.executor == "codebuddy"


async def test_router_skips_when_disabled(monkeypatch):
    router, _, _, _ = _router(monkeypatch, FakeLLM('{"backend":"dsh"}'))
    router.settings = _Settings(enabled=False)
    item = _item()
    assert await router.maybe_route(item) is None
    assert item.executor == "codebuddy"


async def test_router_swallows_unexpected_errors(monkeypatch):
    # 后端探测炸了也不能阻断派发
    def boom(*args, **kwargs):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(executor_route, "build_registry", boom)
    monkeypatch.setattr(executor_route, "llm_from_settings", lambda s: FakeLLM(""))
    router = ExecutorRouter(
        settings=_Settings(), store=_Store(), events=_Events(), scheduler=_Scheduler(),
    )
    item = _item()
    assert await router.maybe_route(item) is None
    assert item.executor == "codebuddy"
