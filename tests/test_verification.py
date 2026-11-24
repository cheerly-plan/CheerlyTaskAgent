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
    body: dict = {"checks": checks, "baseline": "off", **repo}
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

# ── hardening: protected files / caps / env / retry / baseline ────────────────
def _git_repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "T"], check=True
    )
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True
    )
    return repo


def test_protected_file_blocks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(tmp_path, [{"id": "unit", "category": "test", "command": ["true"]}]),
        changed=["pyproject.toml"],
    )
    assert result["ok"] is False
    assert result["state"] == "protected_files_changed"
    assert result["protected_files"] == ["pyproject.toml"]


def test_too_many_files_blocks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["true"]}],
            max_changed_files=2,
        ),
        changed=["a.py", "b.py", "c.py"],
    )
    assert result["ok"] is False
    assert result["state"] == "too_many_files"


def test_env_does_not_leak_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_SECRET", "super-secret-value")
    result = _gate(
        tmp_path,
        _config(tmp_path, [{
            "id": "unit", "category": "test",
            "command": ["sh", "-c", 'test -z "$MY_SECRET"'],
        }]),
    )
    assert result["ok"] is True, result


def test_output_redacts_secrets(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(tmp_path, [{
            "id": "unit", "category": "test",
            "command": ["sh", "-c", "echo api_key=abc123"],
        }]),
    )
    tail = result["checks"][0]["tail"]
    assert "abc123" not in tail
    assert "***" in tail


def test_flaky_retry_passes(tmp_path: Path) -> None:
    counter = tmp_path / "count"
    script = tmp_path / "flaky.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'n=$(cat "{counter}" 2>/dev/null || echo 0)\n'
        "n=$((n+1))\n"
        f'echo "$n" > "{counter}"\n'
        '[ "$n" -ge 2 ]\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{
                "id": "unit", "category": "test",
                "command": ["sh", str(script)],
            }],
            retries=1,
            retry_delay_s=0,
        ),
    )
    assert result["ok"] is True
    assert result["state"] == "passed_with_warnings"
    assert result["checks"][0]["attempts"] == 2


def test_baseline_advisory_allows_preexisting(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["false"]}],
            baseline="advisory",
        ),
        worktree=repo,
    )
    assert result["ok"] is True, result
    assert result["state"] == "passed_with_warnings"
    assert result["checks"][0]["pre_existing"] is True
    assert result["checks"][0]["baseline"]["ok"] is False
    assert not list((tmp_path / "data" / "verification-baselines").glob("*"))


def test_baseline_strict_blocks(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["false"]}],
            baseline="strict",
        ),
        worktree=repo,
    )
    assert result["ok"] is False
    assert result["state"] == "failed"
    assert "基线同样失败" in result["reason"]


def test_total_timeout_blocks(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [
                {"id": "a", "category": "test", "command": ["sleep", "0.45"]},
                {"id": "b", "category": "lint", "command": ["true"]},
            ],
            total_timeout_s=0.6,
            retries=0,
        ),
    )
    assert result["ok"] is False
    assert result["state"] == "failed"
    assert "总验证超时" in result["reason"]


def test_gc_verification_logs(tmp_path: Path) -> None:
    import os
    import time

    from taskagent.verification import gc_verification_logs

    root = tmp_path / "data" / "verifications"
    root.mkdir(parents=True)
    old = root / "old.log"
    new = root / "new.log"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")
    old_time = time.time() - 9 * 86_400
    os.utime(old, (old_time, old_time))
    assert gc_verification_logs(tmp_path / "data") == 1
    assert not old.exists()
    assert new.exists()


def test_diff_too_large_blocks(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text(
        "VALUE = 1\nVALUE = 2\nVALUE = 3\nVALUE = 4\n", encoding="utf-8"
    )
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["true"]}],
            max_diff_lines=2,
        ),
        changed=["app.py"],
        worktree=repo,
    )
    assert result["ok"] is False
    assert result["state"] == "diff_too_large"


def test_na_expiry_blocks(tmp_path: Path) -> None:
    from taskagent.verification import parse_na

    parsed = parse_na({"typecheck": {"reason": "no mypy", "expires_at": "2999-01-01"}})
    assert parsed["typecheck"]["expired"] is False
    parsed = parse_na({"typecheck": {"reason": "no mypy", "expires_at": "2000-01-01"}})
    assert parsed["typecheck"]["expired"] is True
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{"id": "unit", "category": "test", "command": ["true"]}],
            waive_missing=False,
            na={
                "lint": {"reason": "no linter"},
                "typecheck": {"reason": "no mypy", "expires_at": "2999-01-01"},
                "build": {"reason": "no build", "expires_at": "2000-01-01"},
            },
        ),
    )
    assert result["ok"] is False
    assert result["state"] == "incomplete"
    assert "build" in result["missing_categories"]
    assert result["expired_na"] == ["build"]


def test_cpu_rlimit_stops_runaway_check(tmp_path: Path) -> None:
    result = _gate(
        tmp_path,
        _config(
            tmp_path,
            [{
                "id": "cpu", "category": "test",
                "command": ["sh", "-c", "while :; do :; done"],
                "timeout_s": 5,
                "rlimits": {"cpu_s": 1},
            }],
            retries=0,
        ),
    )
    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["checks"][0]["timed_out"] is False
