#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在后台起一个「等 DSH 退出 → 验证安全模式」的独立进程，不占用窗口。

为什么需要这个：验证必须 DSH 完全退出，而发起它的会话本身跑在 DSH 里。
所以这里用 pythonw + DETACHED_PROCESS 起一个不受 DSH 影响的进程，输出进日志文件。

用法:
  python tools/start_wait_verify.py               # 默认等 2 小时
  python tools/start_wait_verify.py --timeout 600
  python tools/start_wait_verify.py --no-relaunch # 验证完不自动重开 DSH
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "wait-verify.log"
PYTHONW = paths.pythonw()


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    timeout = "7200"
    if "--timeout" in argv:
        timeout = argv[argv.index("--timeout") + 1]
    args = [str(PYTHONW if PYTHONW.exists() else sys.executable),
            str(ROOT / "tools" / "wait_and_verify.py"), "--timeout", timeout]
    if "--no-relaunch" not in argv:
        args.append("--relaunch")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logf = LOG.open("ab")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S").encode("utf-8")
    logf.write(f"\n===== {stamp.decode()} 启动等待器 timeout={timeout}s =====\n".encode("utf-8"))
    logf.flush()
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(args, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                            stdout=logf, stderr=subprocess.STDOUT,
                            creationflags=flags, close_fds=True)
    print(f"已启动后台等待器 pid={proc.pid}")
    print(f"日志：{LOG}")
    print("现在什么都不用点：你只要关掉 DSH，它自己会验证安全模式、写报告、再把 DSH 正常开回来。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
