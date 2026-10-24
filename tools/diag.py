#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键诊断：插件到底加载到哪一步。

  python tools/diag.py

顺序回答四个问题：
  1. 宿主半边起来了吗（端口 + 日志）
  2. bundle 被解析了吗（cordis.yml + junction）
  3. 客户端半边跑了吗（client-diag.jsonl 里有没有 module-load / apply）
  4. 插槽注入成功了吗（inject-fired / register-ok）
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = paths.dsh_home()
STATE = ROOT / "state"
DIAG = STATE / "client-diag.jsonl"
PORT_BASE, PORT_SPAN = 43891, 10


def head(t):
    print(f"\n=== {t} ===")


def check_port():
    for p in range(PORT_BASE, PORT_BASE + PORT_SPAN):
        s = socket.socket()
        s.settimeout(0.3)
        ok = s.connect_ex(("127.0.0.1", p)) == 0
        s.close()
        if ok:
            print(f"✓ 宿主桥在听：127.0.0.1:{p}")
            return p
    print("✗ 43891–43900 都没监听 → 宿主半边没起来（看 desktop-next.log 里 @local/dsh-guard-ui）")
    return None


def check_bundle():
    pkg = HOME / "profiles/desktop/package.json"
    cordis = HOME / "profiles/desktop/cordis.yml"
    link = HOME / "profiles/desktop/node_modules/@local/dsh-guard-ui"
    try:
        d = json.loads(pkg.read_text(encoding="utf-8-sig"))
        in_deps = "@local/dsh-guard-ui" in (d.get("dependencies") or {})
        in_bundles = "@local/dsh-guard-ui" in d["dsh"]["profile"]["bundles"]
        print(f"{'✓' if in_deps else '✗'} package.json dependencies")
        print(f"{'✓' if in_bundles else '✗'} package.json dsh.profile.bundles")
    except Exception as e:
        print(f"✗ 读 package.json 失败：{e}")
    print(f"{'✓' if link.exists() else '✗'} node_modules/@local/dsh-guard-ui 软链")
    txt = cordis.read_text(encoding="utf-8", errors="ignore") if cordis.exists() else ""
    print(f"{'✓' if 'guard-ui' in txt else '✗'} cordis.yml 里有 guard-ui 条目")


def check_client():
    if not DIAG.exists():
        print("✗ 还没有 state/client-diag.jsonl")
        return []
    rows = []
    for line in DIAG.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    browser = [r for r in rows if (r.get("extra") or {}).get("href") or r.get("event") in
               ("module-load", "apply", "inject-call", "inject-fired", "register-ok",
                "register-error", "inject-error", "mounted")]
    if not browser:
        print("✗ 没有浏览器来的埋点 → 客户端半边没被执行（宿主起来了也没用）")
        print("  下一步：完全重启 DSH（不是刷新），然后重跑本诊断")
        return []
    print(f"✓ 收到 {len(browser)} 条浏览器埋点")
    for r in browser[-12:]:
        ev = r.get("event")
        extra = r.get("extra") or {}
        detail = extra.get("tag") or extra.get("slot") or ""
        msg = extra.get("message") or ""
        print(f"   {r.get('t','')[11:19]} {ev:15s} {detail} {msg[:60]}")
    ok = [r for r in rows if r.get("event") == "register-ok"]
    return ok


def check_guard():
    hb = STATE / "heartbeat.json"
    ctl = STATE / "control.json"
    try:
        c = json.loads(ctl.read_text(encoding="utf-8"))
        print(f"开关 enabled={c.get('enabled')}")
    except Exception:
        print("开关文件读不到（默认开启）")
    try:
        h = json.loads(hb.read_text(encoding="utf-8"))
        import time
        age = time.time() - hb.stat().st_mtime
        print(f"守护 pid={h.get('pid')} 心跳 {age:.0f}s 前 "
              f"{'（活着）' if age < 90 else '（过期，可能没在跑）'}")
    except Exception:
        print("心跳文件读不到 → 守护没在跑")


def check_autostart():
    """自启两条腿：HKCU Run（登录拉起守护）+ 两个计划任务（每日快照 / 5 分钟看护）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
             "/v", "DSH-Guard"],
            capture_output=True, text=True, timeout=10)
        ok = out.returncode == 0 and "DSH-Guard" in (out.stdout or "")
        print(f"{'✓' if ok else '✗'} HKCU Run 项 DSH-Guard")
    except Exception as e:
        print(f"? HKCU Run 查询失败：{e}")
    for name in ("DSH-Guard-Daily-Snapshot", "DSH-Guard-Ensure"):
        try:
            out = subprocess.run(["schtasks", "/query", "/tn", name, "/fo", "list"],
                                 capture_output=True, text=True, timeout=15)
            ok = out.returncode == 0
            nxt = ""
            for line in (out.stdout or "").splitlines():
                if "Next Run Time" in line or "下次运行时间" in line:
                    nxt = line.split(":", 1)[-1].strip()
            print(f"{'✓' if ok else '✗'} 计划任务 {name}" + (f"（下次 {nxt}）" if nxt else ""))
        except Exception as e:
            print(f"? 计划任务 {name} 查询失败：{e}")


def main() -> int:
    print("dsh-guard-ui 诊断")
    head("1. 宿主桥")
    port = check_port()
    head("2. bundle 解析")
    check_bundle()
    head("3. 客户端半边")
    ok = check_client()
    head("4. 守护")
    check_guard()
    head("5. 自启与定时任务")
    check_autostart()
    head("结论")
    if not port:
        print("宿主没起来 → 看桌面日志里 @local/dsh-guard-ui 的报错")
    elif not ok:
        print("宿主 OK，客户端没注册成功 → 重启 DSH 后重跑；若仍不行，把上面第 3 段发我")
    else:
        print(f"注册成功 {len(ok)} 处，按钮应该出现了；没出现就是插槽位置不对，按 register-ok 的 slot 名调")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
