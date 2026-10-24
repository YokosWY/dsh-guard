#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 DSH 自带的安全模式（--next-safe-mode）。

前提：DSH 必须已经完全退出——有单实例锁，运行中带参数启动只会聚焦旧窗口然后退出
（2026-10-24 实测：run.start 计数不变、safe-mode 数据目录不创建）。

它做四件事：
  1. 确认没有 DSH 进程（有就退出，不硬来）
  2. 记录启动时刻，用 --next-safe-mode 拉起
  3. 等日志里出现 `Host ready: desktop (safe mode)`，并检查 electron-user-data\\safe-mode 是否被创建
  4. 给 PASS / FAIL 结论；默认把安全模式留着让你看，--close 则验证完自动关掉

用法:
  python tools/verify_safe_mode.py            # 验证并留着（在安全模式里）
  python tools/verify_safe_mode.py --close    # 验证完关掉，方便你正常重开
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = Path(r"D:\AI\DSH-NEXT")
LOGS = HOME / "logs"
EXE = HOME / "app" / "DSH NEXT.exe"
CWD = HOME / "app"
SAFE_DATA = HOME / "electron-user-data" / "safe-mode"
TIMEOUT = 120


def dsh_processes() -> list[str]:
    try:
        import psutil  # type: ignore
    except Exception:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name in ("dsh next.exe", "deepseek harness.exe"):
                out.append(f"{p.info['pid']}:{p.info['name']}")
        except Exception:
            continue
    return out


def tail_contains(since: datetime, needle: str) -> str | None:
    """在今日及前一日的 jsonl 里找启动之后的某行。"""
    files = sorted(LOGS.glob("desktop-next-*.jsonl"))[-2:]
    for f in reversed(files):
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines[-4000:]):
            if needle not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            try:
                t = datetime.fromisoformat(o["time"].replace("Z", "+00:00"))
            except Exception:
                continue
            if t >= since:
                return f"{o['time']} {o.get('source')}: {str(o.get('message'))[:120]}"
    return None


def main(argv: list[str]) -> int:
    procs = dsh_processes()
    if procs:
        print(f"✗ DSH 还在跑（{len(procs)} 个进程）：{', '.join(procs[:6])}")
        print("  有单实例锁：带参数启动只会聚焦旧窗口然后退出，测不出结果。")
        print("  先完全退出 DSH（关窗口 + 托盘退出；仍不退出就在任务管理器结束全部 DSH）。")
        return 3
    if not EXE.exists():
        print(f"✗ 找不到 {EXE}")
        return 2

    print("▶ 用 --next-safe-mode 启动…")
    since = datetime.now(timezone.utc)
    subprocess.Popen([str(EXE), "--next-safe-mode"], cwd=str(CWD), close_fds=True)

    hit = None
    t0 = time.time()
    while time.time() - t0 < TIMEOUT:
        hit = tail_contains(since, "Host ready: desktop (safe mode)")
        if hit:
            break
        time.sleep(2)

    safe_dir = SAFE_DATA.exists()
    print()
    print("=== 结果 ===")
    print(f"  安全模式 ready 标记：{'✓ ' + hit if hit else '✗ 没等到（' + str(TIMEOUT) + 's）'}")
    print(f"  electron-user-data\\safe-mode：{'✓ 已创建' if safe_dir else '✗ 未创建'}")
    ok = bool(hit) and safe_dir
    print(f"  结论：{'✅ 安全模式可用（不动原 Profile，退出后自动丢弃临时数据）' if ok else '✗ 未验证通过'}")

    if not ok:
        print("\n  兜底：日志里看 dsh-app:// 或 runtime.failed 相关的报错；")
        print("  原 Profile 未被改动，正常重开即可。")
        return 1

    if "--close" in argv:
        print("\n▶ 验证通过，关掉安全模式实例…")
        try:
            import psutil  # type: ignore
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if (p.info.get("name") or "").lower() == "dsh next.exe":
                        p.terminate()
                except Exception:
                    continue
            time.sleep(4)
            print("  已发送退出；现在可以正常启动 DSH。")
        except Exception as e:
            print(f"  ✗ 关不掉：{e}，请手动关窗口。")
    else:
        print("\n  安全模式窗口留着给你看。看完关掉，再正常启动即回到原 Profile。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
