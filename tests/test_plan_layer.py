"""方案层验收测试：多方案生成、评分排序、未确认拦截、确认幂等。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taskagent.api import create_app
from taskagent.config import Settings
from taskagent.models import ItemKind, ItemStatus, LocalRepo, SourceType, WorkItem
from taskagent.plans import (
    ERR_INVALID_TRANSITION,
    ERR_PLAN_NOT_CONFIRMED,
    PlanError,
    PlanStatus,
    PlanStore,
    confirm_plan,
    generate_candidates,
    is_complex,
    normalise_plan_layer,
    reject_plan,
)

ENABLED = {"enabled": True, "min_steps": 4, "min_candidates": 2}


def _item(tmp_path: Path) -> WorkItem:
    item = WorkItem.create(
        title="补齐方案层",
        description="恢复多方案 + 评分 + 人工确认态",
        kind=ItemKind.FEATURE.value,
        source=SourceType.MANUAL.value,
    )
    item.repo = LocalRepo(path=str(tmp_path), origin="", name="repo-x")
    item.status = ItemStatus.READY
    item.spec = {
        "goal": "复杂需求必须有确认方案才能执行",
        "acceptance": ["方案不少于两个", "按分数降序", "未确认拦截", "确认幂等"],
        "test_hints": ["新增单测"],
        "risk": "high",
    }
    return item


def test_generate_candidates_required_fields_and_score_order(tmp_path: Path):
    item = _item(tmp_path)
    plans = generate_candidates(item, ENABLED)
    assert len(plans) >= 2
    required = {
        "plan_id", "requirement_id", "title", "steps", "score",
        "score_breakdown", "rationale", "status", "created_at",
    }
    for plan in plans:
        assert required.issubset(plan.to_dict().keys())
        assert plan.requirement_id == item.id
        assert plan.steps
        assert 0.0 <= plan.score <= 100.0
        assert set(plan.score_breakdown) == {"clarity", "risk", "scope", "trace"}
        assert all(0.0 <= value <= 100.0 for value in plan.score_breakdown.values())
        assert plan.status == PlanStatus.PENDING_CONFIRMATION
    assert [plan.score for plan in plans] == sorted(
        (plan.score for plan in plans), reverse=True
    )
    # 纯规则、幂等：重复生成得到相同 plan_id 与评分。
    again = generate_candidates(item, ENABLED)
    assert [p.plan_id for p in again] == [p.plan_id for p in plans]
    assert [p.score for p in again] == [p.score for p in plans]


def test_complexity_rule_is_explicit_and_configurable(tmp_path: Path):
    item = _item(tmp_path)
    assert not is_complex(item)                       # 默认关闭
    assert not is_complex(item, {"enabled": False, "min_steps": 1})
    assert is_complex(item, ENABLED)                  # 开关 + 阈值
    assert is_complex(item, {"enabled": True, "min_steps": 99,
                             "require_for_all": True})
    assert not is_complex(
        item, {"enabled": True, "min_steps": 99, "force_kinds": ["bug"]}
    )
    item.is_container = True
    assert not is_complex(item, {"enabled": True, "min_steps": 1})


def test_state_machine_supersedes_siblings_and_rejects_illegal(tmp_path: Path):
    store = PlanStore(tmp_path / "plans")
    item = _item(tmp_path)
    plans = generate_candidates(item, ENABLED)
    store.save(plans)

    chosen = plans[-1]                                # 故意选低分方案
    confirmed, after = confirm_plan(store, item.id, chosen.plan_id, operator="alice")
    assert confirmed.status == PlanStatus.CONFIRMED
    assert confirmed.operator == "alice"
    assert confirmed.decided_at
    others = [p for p in after if p.plan_id != chosen.plan_id]
    assert others and all(p.status == PlanStatus.SUPERSEDED for p in others)
    assert all(p.operator == "alice" for p in others)

    before_bytes = (store.root / f"{item.id}.json").read_bytes()
    for bad in (
        lambda: reject_plan(store, item.id, chosen.plan_id, operator="bob"),
        lambda: confirm_plan(store, item.id, others[0].plan_id, operator="bob"),
    ):
        with pytest.raises(PlanError) as exc:
            bad()
        assert exc.value.code == ERR_INVALID_TRANSITION
        assert (store.root / f"{item.id}.json").read_bytes() == before_bytes


def test_confirm_is_idempotent_and_persists_across_restart(tmp_path: Path):
    root = tmp_path / "plans"
    store = PlanStore(root)
    item = _item(tmp_path)
    plans = generate_candidates(item, ENABLED)
    store.save(plans)
    chosen = plans[0]

    first, _ = confirm_plan(store, item.id, chosen.plan_id, operator="alice")
    snapshot = (root / f"{item.id}.json").read_bytes()
    second, _ = confirm_plan(store, item.id, chosen.plan_id, operator="bob")
    assert second.plan_id == first.plan_id
    assert second.status == PlanStatus.CONFIRMED
    assert second.operator == "alice"                 # 未被第二次覆盖
    assert (root / f"{item.id}.json").read_bytes() == snapshot

    # 新实例 = 进程重启后仍能查到同样状态与排序。
    fresh = PlanStore(root)
    loaded = fresh.list_by_requirement(item.id)
    assert [p.plan_id for p in loaded] == [p.plan_id for p in plans]
    assert fresh.confirmed_plan(item.id).plan_id == chosen.plan_id


@pytest.mark.asyncio
async def test_run_blocked_without_confirmed_plan_has_no_side_effects(env):
    scheduler, store, _gitmap, events, data = env
    plan_store = PlanStore(data / "plans")
    scheduler.settings.plan_store = plan_store
    scheduler.settings.plan_policy = lambda: ENABLED
    item = _item(data)
    store.save(item)
    plan_store.save(generate_candidates(item, ENABLED))

    item_file = data / "tasks" / f"{item.id}.json"
    before_item = item_file.read_bytes()
    before_reports = {p.name for p in (data / "reports").glob("*")}
    before_events = len(events.replay())
    before_tasks = set(scheduler._tasks)

    with pytest.raises(PlanError) as exc:
        scheduler.run(item.id, exec_mode="dry-run")
    assert exc.value.code == ERR_PLAN_NOT_CONFIRMED

    assert item_file.read_bytes() == before_item
    assert {p.name for p in (data / "reports").glob("*")} == before_reports
    assert len(events.replay()) == before_events
    assert set(scheduler._tasks) == before_tasks          # 没有任务入队
    assert store.get(item.id).status == ItemStatus.READY


@pytest.mark.asyncio
async def test_run_after_confirm_executes_only_confirmed_plan(env):
    scheduler, store, _gitmap, _events, data = env
    plan_store = PlanStore(data / "plans")
    scheduler.settings.plan_store = plan_store
    scheduler.settings.plan_policy = lambda: ENABLED
    item = _item(data)
    store.save(item)
    plans = generate_candidates(item, ENABLED)
    plan_store.save(plans)
    chosen = plans[-1]
    confirm_plan(plan_store, item.id, chosen.plan_id, operator="alice")

    scheduler.run(item.id, exec_mode="dry-run")
    queued = store.get(item.id)
    assert queued.selected_plan_id == chosen.plan_id
    assert queued.plan == chosen.steps_text()

    for _ in range(100):
        await asyncio.sleep(0.02)
        current = store.get(item.id)
        if current.is_terminal():
            break
    assert current.status == ItemStatus.SUCCEEDED
    assert current.result is not None and current.result.dry_run
    assert current.selected_plan_id == chosen.plan_id


@pytest.mark.asyncio
async def test_gate_cannot_be_bypassed_after_generation(env):
    """生成方案后改 spec / 关开关，都不能把待确认需求变成可执行。"""
    scheduler, store, _gitmap, _events, data = env
    plan_store = PlanStore(data / "plans")
    scheduler.settings.plan_store = plan_store
    scheduler.settings.plan_policy = lambda: ENABLED
    item = _item(data)
    store.save(item)
    plan_store.save(generate_candidates(item, ENABLED))
    item.plan_required = True
    store.save(item)

    # 模拟执行前的绕过尝试：关掉方案层开关 + 删掉验收项。
    scheduler.settings.plan_policy = lambda: {"enabled": False}
    fresh = store.get(item.id)
    fresh.spec = {"goal": "被简化后的需求"}
    store.save(fresh)
    before = (data / "tasks" / f"{item.id}.json").read_bytes()

    with pytest.raises(PlanError) as exc:
        scheduler.run(item.id, exec_mode="dry-run")
    assert exc.value.code == ERR_PLAN_NOT_CONFIRMED
    assert (data / "tasks" / f"{item.id}.json").read_bytes() == before


def test_api_plans_flow_and_unconfirmed_409(tmp_path: Path):
    data = tmp_path / "data"
    settings = Settings(data_dir=data, project_roots=())
    data.mkdir(parents=True, exist_ok=True)
    ui = settings.load_ui_settings()
    ui["plan_layer"] = {"enabled": True, "min_steps": 1, "min_candidates": 2}
    settings.save_ui_settings(ui)
    repo = tmp_path / "repo"
    repo.mkdir()

    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post(
            "/api/mappings", json={"name": "demo", "path": str(repo)}
        ).status_code == 201
        created = client.post("/api/tasks/manual", json={
            "title": "复杂需求：补齐方案层",
            "description": "多方案 + 评分 + 人工确认",
            "kind": "feature", "repo_name": "demo",
            "exec_mode": "dry-run", "run_now": False,
        })
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        blocked = client.post(
            f"/api/items/{item_id}/run", json={"exec_mode": "dry-run"}
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == ERR_PLAN_NOT_CONFIRMED
        assert client.get(f"/api/items/{item_id}").json()["status"] == "ready"

        generated = client.post(f"/api/items/{item_id}/plans/generate")
        assert generated.status_code == 201, generated.text
        plans = generated.json()["plans"]
        assert len(plans) >= 2
        assert [p["score"] for p in plans] == sorted(
            (p["score"] for p in plans), reverse=True
        )

        chosen = plans[-1]["plan_id"]
        confirmed = client.post(
            f"/api/items/{item_id}/plans/{chosen}/confirm", json={"operator": "tester"}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["plan"]["operator"] == "tester"
        assert confirmed.json()["plan"]["decided_at"]

        listed = client.get(f"/api/items/{item_id}/plans").json()
        assert listed["confirmed_plan_id"] == chosen
        assert [p["score"] for p in listed["plans"]] == sorted(
            (p["score"] for p in listed["plans"]), reverse=True
        )
        assert client.get(f"/api/items/{item_id}").json()["selected_plan_id"] == chosen

        started = client.post(
            f"/api/items/{item_id}/run", json={"exec_mode": "dry-run"}
        )
        assert started.status_code == 200, started.text


def test_no_model_or_auto_path_can_write_confirmed(tmp_path: Path):
    # 生成阶段一律 pending；写 confirmed 的唯一入口是人工 confirm_plan。
    plans = generate_candidates(_item(tmp_path), ENABLED)
    assert all(plan.status == PlanStatus.PENDING_CONFIRMATION for plan in plans)

    src = Path(__file__).resolve().parents[1] / "src" / "taskagent"
    write_pattern = re.compile(
        r"\.status\s*=\s*PlanStatus\.CONFIRMED|"
        r"""["']status["']\s*:\s*["']confirmed["']"""
    )
    for path in src.glob("*.py"):
        if path.name == "plans.py":
            continue
        assert not write_pattern.search(path.read_text(encoding="utf-8")), path.name

    assert normalise_plan_layer(None)["enabled"] is False
