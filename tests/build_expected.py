#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成「期望标签」（独立评分规则，故意与 detect.py 的实现不同）。

为什么要两套规则：
  期望标签只回答「按事件证据，这次启动应该算哪一类」，
  规则写死在这里（±600s 关机窗口、按 first-line 匹配），不看 detect.py 的中间量。
  detect.py 用 -300/+120 的严窗口 —— 故意不同，这样不一致的地方才会暴露出来。

人工裁决：tests/expected.overrides.json 里写 {runId: {family, kind, category, note}}，
本脚本会合并进去，并把每次裁决打印出来，便于复核。

用法:
  python tests/build_expected.py            # 生成 tests/expected.json 并打印分歧
  python tests/build_expected.py --quiet
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import detect as D  # noqa: E402

OUT = ROOT / "tests" / "expected.json"
OVERRIDES = ROOT / "tests" / "expected.overrides.json"
WINDOW = 600  # 评分规则更宽松：±10 分钟内的关机事件都算「机器在关」

RUBRIC = [
    ("host-exit", "host exited with", "crash"),
    ("shell-load", "err_failed", "boot-fail"),
    ("plugin-import", "did not activate", "crash"),
    ("plugin-import", "import failed", "crash"),
    ("config-broken", "not valid json", "boot-fail"),
    ("config-broken", "unexpected token", "boot-fail"),
]

FAMILY = {
    "ok": "clean", "in-progress": "clean", "shutdown": "clean", "quit-forced": "clean",
    "boot-fail": "problem", "crash": "problem", "unclean-unknown": "unknown",
}


def shutdown_near(win, run):
    """因果窗口：run 结束后、下一次启动前，机器是否关机/睡眠。

    不用固定窗口 —— 应用空闲时不写日志，固定 ±N 秒会把「关机杀进程」误判成崩溃。
    """
    horizon = run.next_start
    lower = run.end
    if run.boot_time and lower < run.boot_time:
        lower = run.boot_time
    for e in win:
        if e["id"] in (D.EV_SHUTDOWN | D.EV_SLEEP) and lower <= e["t"] <= (horizon or run.end):
            return e
    return None


def shutdown_sequence_covers(win, t):
    """关机序列覆盖：1074 发起 → 6006 日志停止，杀进程必然发生在其中。"""
    for init in sorted([e for e in win if e["id"] == 1074 and e["t"] <= t], key=lambda e: e["t"],
                       reverse=True):
        ends = [e for e in win if e["id"] in (6006, 13) and e["t"] >= init["t"]]
        stop = min(ends, key=lambda e: e["t"])["t"] if ends else init["t"]
        if init["t"] <= t <= stop:
            return init
    return None


def label(run: D.Run, win: list[dict], now: datetime) -> dict:
    base = {"start": run.start.isoformat(), "duration_s": round(run.duration, 1)}
    if not run.has_run_end and (now - run.end).total_seconds() < D.IN_PROGRESS_WINDOW:
        return {**base, "family": "clean", "kind": "in-progress", "category": None,
                "note": "还在跑"}
    if run.failures:
        msg = str((run.failures[0].get("error") or {}).get("message")
                  or run.failures[0].get("message") or "")
        first = (msg.strip().splitlines() or [""])[0].lower()
        cat, kind = "other", "boot-fail"
        for c, needle, k in RUBRIC:
            if needle in first:
                cat, kind = c, k
                break
        ev = shutdown_sequence_covers(win, D._utc(run.failures[0]["time"]))
        if ev:
            return {**base, "family": "clean", "kind": "shutdown", "category": cat,
                    "note": f"失败发生在关机序列内（1074 {ev['t'].strftime('%H:%M:%SZ')}→6006）"}
        return {**base, "family": "problem", "kind": kind, "category": cat,
                "note": first[:120]}
    if run.has_run_end:
        return {**base, "family": "clean", "kind": "ok", "category": None, "note": "run.end"}
    if run.has_shutdown_start:
        return {**base, "family": "clean", "kind": "quit-forced", "category": None,
                "note": "用户关窗、host 超时被强杀"}
    ev = shutdown_near(win, run)
    if ev:
        return {**base, "family": "clean", "kind": "shutdown", "category": None,
                "note": f"无 run.end，下次启动前机器关机（事件 {ev['id']}）"}
    return {**base, "family": "unknown", "kind": "unclean-unknown", "category": None,
            "note": "无 run.end、无 runtime.failed、下次启动前也无关机事件"}


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    win = D.load_winevents()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    runs = D.analyse()
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}
    expected, disagreements = {}, []
    for rid, r in runs.items():
        exp = label(r, win, now)
        if rid in overrides:
            o = overrides[rid]
            exp.update({k: v for k, v in o.items() if k in ("family", "kind", "category", "note")})
            exp["overridden"] = True
        expected[rid] = exp
        if r.kind != exp["kind"]:
            disagreements.append((r, exp))
    OUT.write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
    if "--quiet" in argv:
        return 0
    print(f"期望标签已写入 {OUT}  共 {len(expected)} 个 run")
    print(f"评分规则与 detect.py 不一致：{len(disagreements)} 个\n")
    for r, exp in sorted(disagreements, key=lambda x: x[0].start):
        print(f"  {r.start.strftime('%m-%d %H:%M')} {r.run_id[:8]}  "
              f"detect={r.kind:16s} expect={exp['kind']:16s}  {exp['note'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
