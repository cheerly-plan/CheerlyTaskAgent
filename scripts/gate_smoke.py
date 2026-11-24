#!/usr/bin/env python3
"""Run the real verification gate against this repository in a throwaway worktree.

This is the CI-level check for the gate itself: it uses the repo's real
pytest/ruff/tsc/build commands (not fake CLIs), so a regression in the gate or
in a generated command shows up before merge.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from taskagent.verification import run_gate

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="taskagent-gate-"))
    worktree = tmp / "worktree"
    added = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(worktree), "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        print(added.stderr or added.stdout, file=sys.stderr)
        return 1
    config = tmp / "verify.json"
    config.write_text(
        json.dumps({
            "repos": {
                "*": {
                    "checks": [
                        {
                            "id": "test", "category": "test",
                            "command": [".venv/bin/python", "-m", "pytest", "-q"],
                            "timeout_s": 900,
                        },
                        {
                            "id": "lint", "category": "lint",
                            "command": [".venv/bin/python", "-m", "ruff", "check", "src", "tests"],
                            "timeout_s": 300,
                        },
                        {
                            "id": "typecheck", "category": "typecheck",
                            "command": ["pnpm", "--dir", "web", "typecheck"],
                            "timeout_s": 300,
                        },
                        {
                            "id": "build", "category": "build",
                            "command": ["pnpm", "--dir", "web", "build"],
                            "timeout_s": 600,
                        },
                    ],
                    "link_dirs": [".venv", "web/node_modules"],
                    "baseline": "off",
                },
            },
        }),
        encoding="utf-8",
    )
    try:
        result = run_gate(
            repo_name="ci-gate",
            repo_path=str(ROOT),
            worktree=str(worktree),
            changed=["README.md"],
            item_id="ci-gate",
            config_path=config,
            data_dir=tmp,
            total_timeout_s=1800,
        )
        print(json.dumps(
            {key: value for key, value in result.items() if key != "checks"},
            ensure_ascii=False,
            indent=2,
        ))
        for check in result["checks"]:
            print(f"{check['id']}: ok={check['ok']} duration={check['duration_s']}s")
        return 0 if result["ok"] else 1
    finally:
        subprocess.run(
            ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    raise SystemExit(main())
