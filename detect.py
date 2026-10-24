#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-guard · 判定器（P0，只读）

从 DSH NEXT 的结构化日志 + Windows 系统事件，判定每次启动的结局。

三条硬规矩（改代码前先读）：
1. 日志按 runId 读，绝不按本地日期读 —— 日志是 UTC 分卷，跨午夜会把一个 run 拆到两个文件里。
2. run.end 不代表正常退出 —— 关机/睡眠杀进程时应用也会写 run.end；反过来 20/24 失败 run 也有 run.end。
3. 判「非崩」靠系统事件（1074/6006/13/42），不靠进程还在不在。

本模块只读，不写 DSH 目录。写入动作全部在 repair/lkg 阶段（P1）。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 输出被重定向时 Windows 会用 cp936，遇到 ✅ 这类字符直接 UnicodeEncodeError；统一成 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

HOME = paths.dsh_home()
LOG_DIR = HOME / "logs"
ROOT = paths.PROJECT
WIN_EVENTS = ROOT / "data" / "winevents.json"

# Windows 系统事件
EV_BOOT = {12, 6005}                     # 本次开机起点
EV_SHUTDOWN = {1074, 6006, 13, 41}       # 关机/重启（1074=发起，6006=事件日志停止，13=关机时间，41=非正常掉电）
EV_SLEEP = {42}                          # 只在判断「结尾附近是否被外部终止」时用
# 注：不要加 event 1（唤醒/时间变更），它一天几十条，会把每次启动都判成「开机后有关机事件」。

SHUTDOWN_LOOKBACK = 300                  # 秒：崩溃前多久内的关机事件算「关机导致」
SHUTDOWN_LOOKAHEAD = 120                 # 秒：崩溃后多久内
FAIL_DELAY_MIN = 2.0                     # 秒：小于此值视为「预 host 阶段失败」
IN_PROGRESS_WINDOW = 600                 # 秒：最后一个事件距今小于此值 → 不判定

CATEGORY_REPAIR = {
    "shell-load": "safe-mode",       # 壳页没起来：配置无关，进安全模式
    "plugin-import": "restore-config",  # 插件 import 失败：回滚配置 + 给 link 插件红牌
    "config-broken": "restore-config",  # 配置语法/BOM 坏：回滚配置
    "host-exit": "safe-mode",        # 后端进程退出：配置无关，进安全模式并出报告
    "silent": "report",              # 静默死：只报告
    "other": "report",
}

_FAIL_PATTERNS = [
    # 顺序即优先级：host exited 的消息体里会带整段 stdout（可能含早先的 import 报错），必须先匹配
    ("host-exit", re.compile(r"host exited with", re.I)),
    ("shell-load", re.compile(r"ERR_FAILED", re.I)),
    ("plugin-import", re.compile(r"did not activate|import failed", re.I)),
    ("config-broken", re.compile(r"not valid JSON|unexpected token|BOM|yaml|parse error", re.I)),
]


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Run:
    run_id: str
    events: list[dict]
    start: datetime | None = None
    end: datetime | None = None
    next_start: datetime | None = None
    home: str = ""
    main_pid: int | None = None
    # 标志
    has_host_start: bool = False
    has_ready: bool = False
    has_shutdown_start: bool = False
    has_run_end: bool = False
    failures: list[dict] = field(default_factory=list)
    # 闸门
    boot_time: datetime | None = None
    gate_boot: bool = False
    gate_shutdown: bool = False
    gate_host: bool = False
    shutdown_hit: dict | None = None
    # 结论
    kind: str = "unknown"
    category: str | None = None
    fail_delay: float | None = None
    repair: str = "none"
    evidence: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if not self.start or not self.end:
            return 0.0
        return (self.end - self.start).total_seconds()


def load_winevents(path: Path = WIN_EVENTS) -> list[dict]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8-sig")
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    out = []
    for e in data:
        try:
            out.append({"t": _utc(e["t"]), "id": int(e["id"])})
        except Exception:
            continue
    out.sort(key=lambda e: e["t"])
    return out


def load_runs(log_dir: Path = LOG_DIR) -> dict[str, Run]:
    """按 runId 聚合所有 UTC 分卷日志。"""
    runs: dict[str, Run] = {}
    files = sorted(log_dir.glob("desktop-next-*.jsonl"))
    for f in files:
        try:
            fh = f.open(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                rid = o.get("runId")
                if not rid:
                    continue
                r = runs.get(rid)
                if r is None:
                    r = runs[rid] = Run(run_id=rid, events=[])
                r.events.append(o)
    for r in runs.values():
        r.events.sort(key=lambda e: e.get("time", ""))
        _fill_flags(r)
    return runs


def _fill_flags(r: Run) -> None:
    if not r.events:
        return
    r.start = _utc(r.events[0]["time"])
    r.end = _utc(r.events[-1]["time"])
    for e in r.events:
        ev = e.get("event") or ""
        msg = str(e.get("message") or "")
        if ev == "run.start":
            if r.main_pid is None:
                r.main_pid = e.get("pid")
            # 用 run.start 本身的时间做起点：它一定是这个 run 的第一条，比「文件里第一条」准
            r.start = _utc(e["time"])
        elif ev == "host.start.start":
            r.has_host_start = True
        elif ev == "shutdown.start":
            r.has_shutdown_start = True
        elif ev == "run.end":
            r.has_run_end = True
        elif ev == "runtime.failed":
            r.failures.append(e)
        if msg.startswith("Host ready"):
            r.has_ready = True
    # 子进程死亡是重要旁证（10-17 两次 network service 崩溃）
    for e in r.events:
        if e.get("event") == "child-process-gone":
            f = e.get("fields") or {}
            r.evidence.append(
                f"child-process-gone: {f.get('service')} reason={f.get('reason')} "
                f"exitCode={f.get('exitCode')}")


def _shutdown_sequence_covers(winevents: list[dict], t: datetime) -> dict | None:
    """判断时刻 t 是否落在一次关机序列里（1074 发起 → 6006 日志停止）。

    关机杀进程必然发生在 1074 之后、6006 之前。用这个序列判断「是关机杀的」，
    比用固定时间窗可靠；也能把「先崩、23 秒后用户才关机」区分出来（那是真崩）。
    """
    inits = [e for e in winevents if e["id"] == 1074 and e["t"] <= t + timedelta(seconds=5)]
    for init in sorted(inits, key=lambda e: e["t"], reverse=True):
        ends = [e for e in winevents if e["id"] in (6006, 13) and e["t"] >= init["t"]]
        stop = min(ends, key=lambda e: e["t"])["t"] if ends else init["t"] + timedelta(seconds=300)
        if init["t"] - timedelta(seconds=5) <= t <= stop + timedelta(seconds=5):
            return init
    return None


def classify(r: Run, winevents: list[dict], now: datetime) -> Run:
    """填 kind/category/gates/repair/evidence。"""
    if not r.start:
        r.kind = "empty"
        return r

    # 闸门 a：本次 run 落在同一个开机会话里
    boots = [e for e in winevents if e["id"] in EV_BOOT and e["t"] <= r.start]
    if boots:
        r.boot_time = boots[-1]["t"]
        # 开机之后、run.start 之前不应有关机事件
        between = [e for e in winevents
                   if e["id"] in EV_SHUTDOWN and r.boot_time <= e["t"] <= r.start]
        r.gate_boot = not between
    else:
        r.gate_boot = False  # 没有开机记录 → 无法确认，保守判否

    # 闸门 b：这个 run 结束之后、下一次启动之前，机器有没有关机/睡眠
    # 不能用固定窗口：应用空闲时几小时不写日志，固定窗口会把「关机杀进程」误判成崩溃。
    horizon = r.next_start or (now + timedelta(seconds=SHUTDOWN_LOOKAHEAD))
    lower = r.end - timedelta(seconds=SHUTDOWN_LOOKBACK)
    if r.boot_time and lower < r.boot_time:
        lower = r.boot_time   # 回看不能越到上一次开机之前，否则会吃到「上次关机」事件
    near = [e for e in winevents
            if e["id"] in (EV_SHUTDOWN | EV_SLEEP) and lower <= e["t"] <= horizon]
    r.gate_shutdown = not near
    r.shutdown_hit = near[0] if near else None

    # 闸门 c：host 起过、失败发生在 host 起来 2s 之后
    r.gate_host = False
    if r.failures:
        first_fail = _utc(r.failures[0]["time"])
        r.fail_delay = (first_fail - r.start).total_seconds()
        host_t = None
        for e in r.events:
            if e.get("event") == "host.start.start":
                host_t = _utc(e["time"])
                break
        r.gate_host = bool(r.has_host_start and r.fail_delay > FAIL_DELAY_MIN)

    # 未结束的 run 不判
    if (now - r.end).total_seconds() < IN_PROGRESS_WINDOW and not r.has_run_end:
        r.kind = "in-progress"
        return r

    if r.failures:
        msg = str((r.failures[0].get("error") or {}).get("message")
                  or r.failures[0].get("message") or "")
        first = msg.strip().splitlines()[0] if msg.strip() else ""
        r.category = "other"
        for cat, pat in _FAIL_PATTERNS:
            if pat.search(first):
                r.category = cat
                break
        r.evidence.append(f"runtime.failed: {first[:160]}")
        fail_t = _utc(r.failures[0]["time"])
        seq = _shutdown_sequence_covers(winevents, fail_t)
        if seq:
            r.kind = "shutdown"
            r.evidence.append(
                f"失败落在关机序列内（1074 {seq['t'].strftime('%H:%M:%SZ')} → 6006），是关机杀进程")
        elif not r.gate_boot:
            r.kind = "shutdown"
            r.evidence.append("开机记录不全，无法确认不是关机导致（保守判非崩）")
        else:
            # 用「有没有到过 Host ready」区分：没到 = 起不来；到了 = 用着崩
            r.kind = "crash" if r.has_ready else "boot-fail"
            later = r.shutdown_hit
            if later and later["t"] > fail_t:
                r.evidence.append(
                    f"（失败后 {int((later['t'] - fail_t).total_seconds())}s 机器才关机，故不算关机导致）")
        r.repair = CATEGORY_REPAIR.get(r.category or "", "none")
        return r

    # 没有 runtime.failed
    if r.has_run_end:
        r.kind = "ok"
        r.evidence.append("run.end")
        return r

    # 用户主动关窗但 host 卡死超 5 秒被强杀 —— 不是崩溃，是关闭路径缺陷
    if r.has_shutdown_start:
        r.kind = "quit-forced"
        r.evidence.append("有 shutdown.start 但没 run.end（关闭超时被强杀）")
        return r

    if not r.gate_shutdown:
        r.kind = "shutdown"               # 关机/睡眠导致，不算崩
        r.evidence.append(f"无 run.end，但结尾附近有系统事件 {r.shutdown_hit['id']}")
        return r

    r.kind = "unclean-unknown"            # 需要人工看的：既没正常退出，也没有关机
    r.category = "silent"
    r.repair = "report"
    r.evidence.append("没有 run.end，也没有 runtime.failed，结尾附近无关机/睡眠事件")
    return r


def analyse(now: datetime | None = None, home: Path | None = None) -> dict[str, Run]:
    """按 home 分析一个 DSH 实例。home 为空时用默认目标。"""
    now = now or datetime.now(timezone.utc)
    log_dir = (Path(home) / "logs") if home else LOG_DIR
    winevents = load_winevents()
    runs = load_runs(log_dir)
    ordered = sorted(runs.values(), key=lambda r: r.start or now)
    for a, b in zip(ordered, ordered[1:]):
        a.next_start = b.start
    for r in runs.values():
        r.home = str(home) if home else str(HOME)
        classify(r, winevents, now)
    return runs


def analyse_all(now: datetime | None = None) -> dict[str, dict[str, Run]]:
    """按 paths.targets() 逐个分析；键是 target 名。"""
    out = {}
    for t in paths.targets():
        out[t["name"]] = analyse(now=now, home=t["home"])
    return out


def summarize(runs: dict[str, Run]) -> dict:
    from collections import Counter
    kinds = Counter(r.kind for r in runs.values())
    cats = Counter(r.category for r in runs.values() if r.category)
    crash_kinds = {"crash", "boot-fail", "unclean-unknown"}
    return {
        "runs": len(runs),
        "kinds": dict(kinds),
        "categories": dict(cats),
        "real_problems": sum(v for k, v in kinds.items() if k in crash_kinds),
        "recoverable_by_config": sum(
            1 for r in runs.values() if r.repair == "restore-config"),
    }


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    def dump_json(groups: dict[str, dict[str, Run]]) -> None:
        out = {}
        for name, runs in groups.items():
            out[name] = {rid: {
                "home": r.home,
                "start": r.start.isoformat() if r.start else None,
                "duration_s": round(r.duration, 1),
                "kind": r.kind,
                "category": r.category,
                "repair": r.repair,
                "fail_delay": round(r.fail_delay, 1) if r.fail_delay is not None else None,
                "gates": {"boot": r.gate_boot, "shutdown": r.gate_shutdown, "host": r.gate_host},
                "evidence": r.evidence,
            } for rid, r in sorted(
                runs.items(),
                key=lambda kv: kv[1].start or datetime.min.replace(tzinfo=timezone.utc))}
        print(json.dumps(out, ensure_ascii=False, indent=1))

    # --all 逐个目标；--home 指定单个；否则默认目标
    if "--all" in argv:
        groups = analyse_all()
    elif "--home" in argv:
        home = Path(argv[argv.index("--home") + 1])
        if not (home / "logs").is_dir():
            print(f"✗ {home} 下没有 logs 目录，不像 DSH 数据目录")
            print("  用法：python detect.py --home <DSH数据目录>  或  python detect.py --all")
            return 2
        groups = {home.name: analyse(home=home)}
    else:
        groups = {"default": analyse()}

    if "--json" in argv:
        dump_json(groups)
        return 0

    if "--csv" in argv:
        import csv
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(["target", "run_id", "start", "duration_s", "kind",
                    "category", "repair", "boot", "shutdown", "host"])
        for name, runs in groups.items():
            for rid, r in sorted(
                    runs.items(),
                    key=lambda kv: kv[1].start or datetime.min.replace(tzinfo=timezone.utc)):
                w.writerow([name, rid,
                            r.start.isoformat() if r.start else "",
                            round(r.duration, 1), r.kind, r.category or "",
                            r.repair, r.gate_boot, r.gate_shutdown, r.gate_host])
        return 0

    for name, runs in groups.items():
        s = summarize(runs)
        label = f"[{name}] " if len(groups) > 1 else ""
        print(f"{label}run 总数 {s['runs']}  需要处理 {s['real_problems']}  "
              f"可由配置恢复 {s['recoverable_by_config']}")
        print(f"{label}结局分布: {s['kinds']}")
        print(f"{label}崩因分布: {s['categories']}")
        if "--list" in argv:
            for rid, r in sorted(
                    runs.items(),
                    key=lambda kv: kv[1].start or datetime.min.replace(tzinfo=timezone.utc)):
                stamp = r.start.strftime("%m-%d %H:%M") if r.start else "?"
                print(f"  {stamp} {rid[:8]} {r.kind:16s} {str(r.category or '-'):14s} "
                      f"dur={r.duration:7.1f}s repair={r.repair}")
        if len(groups) > 1:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
