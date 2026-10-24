#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按 config.json 的 targets 逐个备份（配置快照 + 会话增量）。

一台机器上同时装 NEXT 和官方版时用这个；只装一套也能跑（targets 缺省就是一个默认目标）。

用法（计划任务里也调它）:
  python tools/backup_all.py
  python tools/backup_all.py --only next
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import lkg  # noqa: E402
import paths  # noqa: E402
import sessions  # noqa: E402


def main(argv: list[str]) -> int:
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]

    targets = paths.targets()
    if only:
        targets = [t for t in targets if t["name"] == only]
        if not targets:
            print(f"✗ 没有名为 {only} 的目标")
            return 2

    rc = 0
    for idx, t in enumerate(targets):
        raw_name, home = t["name"], Path(t["home"])
        # 第一个目标用 default 标签（rescue / lkg 默认操作的就是它），其余用各自名字分目录
        name = "default" if idx == 0 else raw_name
        print(f"\n=== [{name}] {home} ===")
        if not home.exists():
            print("  ✗ 目录不存在，跳过")
            rc = 1
            continue
        # 配置快照：cfg 里换 home，label 决定落到哪个子目录
        cfg = lkg.load_config()
        cfg["home"] = str(home)
        try:
            m = lkg.snapshot("daily", cfg=cfg, quiet=False,
                             skip_identical=True, label=name)
            print(f"  配置快照：{m['id']}（{m['file_count']} 文件 / "
                  f"{m['bytes']/1048576:.2f} MB）")
        except Exception as e:
            print(f"  ✗ 配置快照失败：{type(e).__name__}: {e}")
            rc = 1
        # 会话增量
        try:
            sessions.SRC = home / "sessions"
            sessions.snapshot(quiet=False, label=name)
        except Exception as e:
            print(f"  ✗ 会话备份失败：{type(e).__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
