#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-guard · 一键救援（P1，人工触发；不常驻、不自动）

它只做三件事，按顺序：
  1. 体检（只读）
  2. 配置类问题 → 从基线还原（要求 DSH 已完全退出；默认只演算，加 --apply 才写）
  3. 其它问题 → 给你安全模式启动命令（--safe-mode 直接启）

用法:
  python rescue.py                 # 只体检 + 给建议，什么都不改
  python rescue.py --apply         # 允许还原（会再问一次）
  python rescue.py --apply --yes   # 不问了（给 .cmd 用）
  python rescue.py --safe-mode     # 用 --next-safe-mode 启动（要求无 DSH 进程）
  python rescue.py --launch        # 正常启动（要求无 DSH 进程）
退出码: 0 一切正常/已修好；2 有阻塞问题没解决；3 DSH 没退出；4 还原失败（已回滚）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 输出被重定向时 Windows 会用 cp936，遇到 ✅ 这类字符直接 UnicodeEncodeError；统一成 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import lkg  # noqa: E402
import preflight  # noqa: E402

APP_EXE = lkg.HOME / "app" / "DSH NEXT.exe"
APP_CWD = lkg.HOME / "app"

CONFIG_CODES = {"bom", "json-parse", "yaml-parse", "yaml-shape", "missing", "bundle-missing"}


def ask(prompt: str, assume_yes: bool = False, default: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        ans = input(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        return default
    return ans in ("y", "yes", "是")


def run_preflight() -> tuple[int, list[dict]]:
    preflight.results.clear()
    rc = preflight.run(do_drift=True)
    return rc, list(preflight.results)


def launch(safe_mode: bool) -> int:
    procs = lkg._dsh_processes()
    if procs:
        print(f"✗ DSH 还在跑（{', '.join(procs)}），先完全退出。")
        return 3
    if not APP_EXE.exists():
        print(f"✗ 找不到 {APP_EXE}")
        return 2
    args = [str(APP_EXE)] + (["--next-safe-mode"] if safe_mode else [])
    subprocess.Popen(args, cwd=str(APP_CWD), close_fds=True)
    mode = "安全模式（临时环境，退出即回原 Profile）" if safe_mode else "正常模式"
    print(f"▶ 已用{mode}启动：{' '.join(args)}")
    lkg._log_incident("launch-safe-mode" if safe_mode else "launch", "-", mode, [])
    return 0


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 62)
    print("dsh-guard 救援  ·", lkg.utcnow().astimezone().strftime("%Y-%m-%d %H:%M"))
    print("=" * 62)

    if "--safe-mode" in argv or "--launch" in argv:
        rc, results = run_preflight()
        print(preflight.render(rc))
        print()
        if rc == 2 and not ask("体检有阻塞问题，仍要启动？", assume_yes="--yes" in argv):
            return 2
        return launch(safe_mode="--safe-mode" in argv)

    rc, results = run_preflight()
    print(preflight.render(rc))

    fails = [r for r in results if r["level"] == "FAIL"]
    config_fails = [r for r in fails if r["code"] in CONFIG_CODES]
    procs = lkg._dsh_processes()
    baseline = lkg.load_baseline()

    print()
    if not fails:
        print("结论：配置层没问题，可以直接启动。")
        if procs:
            print(f"  但 DSH 正在运行（{len(procs)} 个进程），不用再启。")
        else:
            print("  要现在启动吗：python rescue.py --launch")
        return 0

    if config_fails:
        print(f"结论：{len(config_fails)} 处配置层问题，可以尝试从基线还原。")
        if not baseline:
            print("  ✗ 还没有基线可还原。先修好这一次，再跑：")
            print("     python lkg.py snapshot && python lkg.py promote <id> --yes")
            lkg._log_incident("rescue-no-baseline", "-", "config broken, no baseline",
                              [r["code"] for r in config_fails])
            return 2
        print(f"  基线：{baseline['id']}（{baseline.get('created', '?')}，{baseline['file_count']} 个文件）")
        if procs:
            print(f"\n✗ DSH 还在跑（{len(procs)} 个进程）。还原前必须完全退出：")
            print("  · 关掉 DSH 主窗口；如果关不掉，用任务管理器结束全部 “DSH NEXT”")
            return 3
        if "--apply" not in argv:
            print("\n先看会改什么（演练，不写）：")
            lkg.restore(baseline["id"], apply=False)
            print("\n确认无误后：python rescue.py --apply")
            return 2
        if not ask(f"确认用基线 {baseline['id']} 覆盖当前配置？",
                   assume_yes="--yes" in argv):
            print("已取消。")
            return 2
        # 先把「坏掉的现在」留一份，万一基线也不对还能回看
        try:
            lkg.snapshot("pre-restore", quiet=True)
            print("  已把当前（将要被替换的）配置存为 pre-restore 快照")
        except Exception as e:
            print(f"  ⚠ pre-restore 快照失败：{e}（继续）")
        code = lkg.restore(baseline["id"], apply=True)
        if code != 0:
            print("✗ 还原失败（已自动回滚），按上面提示手工处理。")
            return 4
        rc2, _ = run_preflight()
        print()
        print(preflight.render(rc2))
        if rc2 == 2:
            print("还原后仍有阻塞问题 → 用安全模式进去看看：python rescue.py --safe-mode")
            return 2
        print("✅ 还原完成。启动：python rescue.py --launch")
        return 0

    print("结论：不是配置层的问题（可能是壳页/后端/环境），还原配置没用。")
    print("  建议：用安全模式启动（不动你的 Profile）：python rescue.py --safe-mode")
    lkg._log_incident("rescue-nonconfig", "-",
                      "; ".join(r["msg"] for r in fails[:3]), [r["code"] for r in fails])
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
