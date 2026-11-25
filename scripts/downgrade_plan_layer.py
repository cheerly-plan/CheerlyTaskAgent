"""回滚「补齐方案层」产生的数据。

只处理本层新增的 ``<data_dir>/plans/*.json`` 中带本层 schema 标记的文件：

- 默认 dry-run：只列出将要删除的文件，不改动磁盘；
- ``--apply`` 才真正删除；不触碰 tasks/、settings.json 或任何其他文件；
- 没有标记的文件（用户自己放的东西）一律跳过。

用法：
    python scripts/downgrade_plan_layer.py --data-dir data           # 预演
    python scripts/downgrade_plan_layer.py --data-dir data --apply   # 执行
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "taskagent.plan_layer.v1"


def plan_files(root: Path) -> list[Path]:
    """Only plan-layer files we wrote ourselves are eligible for removal."""
    if not root.is_dir():
        return []
    targets: list[Path] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == SCHEMA:
            targets.append(path)
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description="回滚方案层数据（只删本层生成的 plans/*.json）")
    parser.add_argument("--data-dir", default="data", help="调度台 data 目录，默认 data")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正删除；不传则只做 dry-run 预演",
    )
    args = parser.parse_args()
    root = Path(args.data_dir).expanduser() / "plans"
    targets = plan_files(root)
    if not targets:
        print(f"没有需要回滚的方案层数据：{root}")
        return
    print(f"{'删除' if args.apply else '预演删除'} {len(targets)} 个方案层文件：")
    for path in targets:
        print(f"  {path}")
    if not args.apply:
        print("dry-run 完成；加 --apply 才会真正删除。")
        return
    for path in targets:
        path.unlink()
    print("方案层数据已回滚；任务文件与其他配置未改动。")


if __name__ == "__main__":
    main()
