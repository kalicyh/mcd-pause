#!/usr/bin/env python3
"""Explicit local installation; preserve an existing differing skill by default."""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil


def fingerprint(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def main():
    parser = argparse.ArgumentParser(description="安装 McPause 技能；不修改 MCP、Token 或定时任务")
    parser.add_argument("--dest", required=True, help="宿主用户技能目录")
    parser.add_argument("--backup-and-replace", action="store_true")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / ".agents/skills/mcd-pause"
    parent = Path(args.dest).expanduser().resolve()
    target = parent / "mcd-pause"
    if target == source or source in target.parents:
        raise SystemExit("目标不能位于技能源码目录中")
    if target.exists():
        if fingerprint(source) == fingerprint(target):
            print(f"已是相同版本：{target}")
            return
        if not args.backup_and_replace:
            raise SystemExit("同名技能已存在且内容不同。需要备份更新时使用 --backup-and-replace。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backups = parent.parent / "mcpause-skill-backups"
        backups.mkdir(parents=True, exist_ok=True)
        backup = backups / ("mcd-pause-" + stamp)
        target.rename(backup)
        print(f"旧版本已备份：{backup}")
    parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"已安装：{target}")


if __name__ == "__main__":
    main()
