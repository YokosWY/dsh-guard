#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""等 DSH 退出 → 自动验证安全模式 → 写报告 →（可选）正常重开。

为什么要有这个：验证必须 DSH 完全退出才能做，而我就跑在 DSH 里面——
DSH 一关，我也没了。所以把这件事交给一个**独立于 DSH 的进程**自己等。

用法（双击 `验证安全模式-自动.cmd` 等价）:
  python tools/wait_and_verify.py                  # 等 20 分钟，验证完留着安全模式窗口
  python tools/wait_and_verify.py --relaunch       # 验证完关掉安全模式、正常重开
  python tools/wait_and_verify.py --timeout 60     # 自定义等待上限（秒）
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
EXE = HOME / "app" / "DSH NEXT.exe"
CWD = HOME / "app"
STATE = ROOT / "state"
REPORTS = ROOT / "reports"
LOGMARK = ROOT / "logs" / "safe-mode-verify.log"


def now() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    line = f"{now().astimezone().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        LOGMARK.parent.mkdir(parents=True, exist_ok=True)
        with LOGMARK.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def dsh_count() -> int:
    try:
        import psutil  # type: ignore
    except Exception:
        return 0
    n = 0
    for p in psutil.process_iter(["name"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name in ("dsh next.exe", "deepseek harness.exe"):
                n += 1
        except Exception:
            continue
    return n


def wait_for_exit(timeout: int) -> bool:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        n = dsh_count()
        if n != last:
            log(f"当前 DSH 进程数：{n}" + ("（等待你关闭）" if n else "（已退出，开始验证）"))
            last = n
        if n == 0:
            return True
        time.sleep(3)
    return False


def main(argv: list[str]) -> int:
    timeout = 1200
    if "--timeout" in argv:
        try:
            timeout = int(argv[argv.index("--timeout") + 1])
        except (IndexError, ValueError):
            pass
    relaunch = "--relaunch" in argv

    log(f"等待 DSH 退出（最多 {timeout} 秒）。请现在关掉 DSH，这个窗口会自己继续。")
    if not wait_for_exit(timeout):
        log("✗ 等超时了，DSH 还没退出。什么都没做，原样保持。")
        return 2

    # 给系统一点时间收尾进程/端口
    time.sleep(3)
    log("▶ 调用 verify_safe_mode.py --close 做验证")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify_safe_mode.py"), "--close"],
        capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    rc = proc.returncode
    log(f"验证结束 rc={rc}")
    for line in out.splitlines():
        log("  " + line)

    ok = rc == 0 and "安全模式可用" in out
    result = {
        "t": now().isoformat(timespec="seconds"),
        "ok": ok,
        "rc": rc,
        "output": out[-4000:],
    }
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "safe-mode-verify.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORTS.mkdir(parents=True, exist_ok=True)
    md = REPORTS / f"safe-mode-{now().astimezone().strftime('%Y%m%d-%H%M')}.md"
    md.write_text(
        f"# 安全模式验证 {now().astimezone().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"- 结论：{'✅ 可用' if ok else '✗ 未通过'}\n- 退出码：{rc}\n\n```\n{out}\n```\n",
        encoding="utf-8")
    log(f"报告已写 {md}")

    if relaunch:
        if EXE.exists():
            log("▶ 正常模式重开 DSH")
            subprocess.Popen([str(EXE)], cwd=str(CWD), close_fds=True)
        else:
            log("✗ 找不到 exe，没重开")
    else:
        log("安全模式/正常模式按当前状态留着；要正常重开请手动启动 DSH。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
