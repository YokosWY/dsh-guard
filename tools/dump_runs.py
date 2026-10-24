#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人工复核用：把一个 run 的时间线 + 前后系统事件打出来，方便逐条定标。

用法:
  python tools/dump_runs.py                 # 只打「可疑」的 run（非 ok / 非 shutdown）
  python tools/dump_runs.py --all           # 全部
  python tools/dump_runs.py --run 8        # runId 前缀
  python tools/dump_runs.py --gaps          # 打每个 run 结束后到下一个 run 开始之间隔了多久
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import detect as D  # noqa: E402

SUSPICIOUS = {"boot-fail", "crash", "unclean-unknown", "quit-forced", "in-progress", "empty"}


def nearest_events(win: list[dict], t, before=3, after=3):
    if t is None:
        return [], []
    before_ev = [e for e in win if e["t"] <= t][-before:]
    after_ev = [e for e in win if e["t"] > t][:after]
    return before_ev, after_ev


def fmt(e):
    return f"{e['t'].strftime('%m-%dT%H:%M:%SZ')} id={e['id']}"


def dump(r: D.Run, win: list[dict]) -> None:
    print(f"\n=== {r.run_id[:8]}  {r.start.strftime('%Y-%m-%dT%H:%M:%SZ')} "
          f"dur={r.duration:.1f}s pid={r.main_pid}")
    print(f"    结局={r.kind}  崩因={r.category}  repair={r.repair}  "
          f"gates(boot={int(r.gate_boot)},shutdown={int(r.gate_shutdown)},host={int(r.gate_host)})")
    print(f"    事件数={len(r.events)}  ready={int(r.has_ready)} host.start={int(r.has_host_start)} "
          f"shutdown.start={int(r.has_shutdown_start)} run.end={int(r.has_run_end)}")
    for ev in r.evidence:
        print(f"    · {ev}")
    before, after = nearest_events(win, r.end)
    print("    系统事件(前):", ", ".join(fmt(e) for e in before) or "-")
    print("    系统事件(后):", ", ".join(fmt(e) for e in after) or "-")
    # 关键事件序列
    key = []
    for e in r.events:
        ev = e.get("event") or ""
        msg = str(e.get("message") or "")
        if ev in ("run.start", "host.start.complete", "shutdown.start", "shutdown.timeout",
                  "run.forced-exit", "run.end", "runtime.failed", "run.previous-unclean") \
                or msg.startswith("Host ready"):
            key.append(f"{e['time'][11:19]} {ev or 'HostReady'}")
    print("    时序:", " | ".join(key[:12]) or "(无关键事件)")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    win = D.load_winevents()
    runs = D.analyse()
    ordered = sorted(runs.values(), key=lambda r: r.start)
    if "--gaps" in argv:
        for a, b in zip(ordered, ordered[1:]):
            gap = (b.start - a.end).total_seconds()
            if gap > 60:
                print(f"{a.kind:16s} {a.start.strftime('%m-%d %H:%M')} → "
                      f"{b.start.strftime('%m-%d %H:%M')}  间隔 {gap/60:7.1f} 分钟")
        return 0
    prefix = None
    if "--run" in argv:
        prefix = argv[argv.index("--run") + 1]
    for r in ordered:
        if prefix:
            if r.run_id.startswith(prefix):
                dump(r, win)
        elif "--all" in argv or r.kind in SUSPICIOUS:
            dump(r, win)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
