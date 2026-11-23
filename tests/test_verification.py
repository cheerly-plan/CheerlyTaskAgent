"""Post-execution verification gate (MVP)."""

from __future__ import annotations

import json
from pathlib import Path

from taskagent.verification import CATEGORIES, is_test_file, load_config, run_gate


def _config(
    tmp_path: Path,
    checks: list[dict],
    *,
    waive_missing: bool = True,
    **repo: object,
) -> Path:
    body: dict = {"checks": checks, **repo}
    if waive_missing:
        present = {str(check.get("category")) for check in checks}
        body["na"] = {
            category: "waived in this test" for category in CATEGORIES
            if category not in present
        }
    path = tmp_path / "verify.json"
    path.write_text(json.dumps({"repos": {"demo": body}}), encoding="utf-8")
    return path


def _worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    return worktree


def _gate(
    tmp_path: Path,
    config: Path,
    *,
    changed: list[str] | None = None,
    worktree: Path | None = None,
) -> dict:
    return run_gate(
        repo_name="demo",
        repo_path=str(tmp_path / "repo"),
        worktree=str(worktree or _worktree(tmp_path)),
        changed=changed if changed is not None else ["app.py"],
        item_id="work-test-1",
        config_path=config,
        data_dir=tmp_path / "data",
    )


def test_test_file_matching() -> None:
    assert is_test_file("tests/test_app.py", ["tests/*"])
    assert is_test_file("web/src/App.test.tsx", ["*.test.*"])
    assert is_test_file("pkg/foo_test.py", ["*_test.py"])
    assert is_test_file("a/b/__tests__/x.ts", ["*/__tests__/*"])
    assert is_test_file("snapshots/a.snap", ["*.snap"])
    assert not is_test_file("src/main.py", ["test_*.py"])
    assert not is_test_file("src/attestation.py", ["test_*.py"])


def test_load_config_tolerates_missing_and_bad_files(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_config(bad) == {}
    assert load_config(_config(tmp_path, [{"command": ["true"]}]))["repos"]["demo"]


def test_unconfigured_blocks(tmp_path: Path) -> None:
    result = _gate(tmp_path, tmp_path / "missing.json")
    assert result["ok"] is False
    assert result["state"] == "unconfigured"


def test_empty_diff_blocks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(tmp_path, [{"id": "unit", "command": ["true"]}]),
        changed=[],
    )
    assert result["ok"] is False
    assert result["state"] == "no_changes"


def test_test_file_change_blocks_before_running_checks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(tmp_path, [{"id": "unit", "command": ["true"]}]),
        changed=["app.py", "tests/test_app.py"],
    )
    assert result["ok"] is False
    assert result["state"] == "test_files_changed"
    assert result["test_files"] == ["tests/test_app.py"]
    assert result["checks"] == []


def test_passing_checks_allow_merge(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [
                {"id": "lint", "label": "lint", "category": "lint", "command": ["true"]},
                {"id": "unit", "label": "unit", "category": "test", "command": ["true"]},
            ],
        ),
    )
    assert result["ok"] is True
    assert result["state"] == "passed"
    assert [check["id"] for check in result["checks"]] == ["lint", "unit"]


def test_failing_check_blocks_and_records_exit_code(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(tmp_path, [{
            "id": "unit", "label": "unit", "category": "test", "command": ["false"],
        }]),
    )
    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["checks"][0]["exit_code"] != 0
    assert "未通过" in result["reason"]


def test_timeout_blocks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{
                "id": "slow", "label": "slow", "category": "test",
                "command": ["sleep", "2"], "timeout_s": 0.2,
            }],
        ),
    )
    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["checks"][0]["timed_out"] is True
    assert "超时" in result["reason"]


def test_link_dirs_before_running_checks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".venv").mkdir(parents=True)
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["true"]}],
            link_dirs=[".venv"],
        ),
        worktree=_worktree(tmp_path),
    )
    assert result["ok"] is True
    assert result["linked_dirs"] == [".venv"]
    assert (tmp_path / "wt" / ".venv").is_symlink()


def test_missing_category_blocks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["true"]}],
            waive_missing=False,
        ),
    )
    assert result["ok"] is False
    assert result["state"] == "incomplete"
    assert set(result["missing_categories"]) == {"lint", "typecheck", "build"}


def test_kill_switch_allows_merge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TASKAGENT_VERIFY", "0")
    result = _gate(tmp_path, tmp_path / "missing.json")
    assert result["ok"] is True
    assert result["state"] == "disabled"