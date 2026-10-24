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
    # 应用还活着但能力受损，修配置没用，只能重启对应进程
    "renderer-gone": "restart-app",
    "network-gone": "restart-app",
    "oom": "free-memory",
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
    # 附加观察（不影响 kind）：子进程崩溃、恢复动作失败、关机不完整等
    gone: list[dict] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    degraded: bool = False          # 应用活着但能力受损（渲染进程/网络服务崩过）
    degradation: list[str] = field(default_factory=list)   # renderer / network / oom
    signals: list[str] = field(default_factory=list)      # 环境类异常（不是崩溃，但影响可用性）
    tool_failures: dict = field(default_factory=dict)     # failureCode -> 次数
    inactive_plugins: list[dict] = field(default_factory=list)  # 没激活成功的插件
    crash_loop: bool = False        # 10 分钟内连续 3 次以上事故

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
            r.gone.append({"type": f.get("type"), "reason": f.get("reason"),
                           "service": f.get("service"), "code": f.get("exitCode")})
            r.evidence.append(
                f"child-process-gone: {f.get('service')} reason={f.get('reason')} "
                f"exitCode={f.get('exitCode')}")

    # 恢复动作失败：应用自己尝试自愈但没成功
    if any((e.get("event") or "") == "commands/recovery-action.failed" for e in r.events):
        r.annotations.append("应用自己的恢复动作失败（commands/recovery-action.failed）")

    # 关机不完整：host 层发起了 shutdown.start 但没等到 shutdown.complete
    # 注意只认 source=host 的那对事件；electron 层的 shutdown.start 每次都有，配套的 complete 不一定存在
    host_started = any((e.get("source") or "") == "host"
                       and (e.get("event") or "") == "shutdown.start" for e in r.events)
    host_done = any((e.get("source") or "") == "host"
                    and (e.get("event") or "") == "shutdown.complete" for e in r.events)
    if host_started and not host_done:
        r.annotations.append("host 关机流程没走完（有 shutdown.start，无 shutdown.complete）")


_LOG_SIGNATURES = [
    ("heap-oom", re.compile(r"heap out of memory|Fatal process out of memory", re.I),
     "宿主内存溢出（Node 堆），进程可能直接消失"),
    ("module-missing", re.compile(r"ERR_MODULE_NOT_FOUND|Cannot find (module|package)", re.I),
     "插件依赖缺失，插件起不来"),
    ("dll-init-failed", re.compile(r"0xC0000142|STATUS_DLL_INIT_FAILED", re.I),
     "子进程 DLL 初始化失败（沙箱或权限问题），shell 工具会全废"),
    ("econnreset", re.compile(r"ECONNRESET", re.I), "连接被重置（网络抖动或代理断流）"),
    ("file-locked", re.compile(r"(EPERM|EBUSY)", re.I), "写入被拦截（杀软/文件被占用），状态可能只落一半"),
    ("port-in-use", re.compile(r"EADDRINUSE", re.I), "端口被占，内置服务起不来"),
    ("disk-full", re.compile(r"ENOSPC|no space left", re.I), "磁盘满"),
]

_DEGRADING = {"heap-oom", "module-missing", "dll-init-failed", "file-locked",
              "port-in-use", "disk-full"}

# 插件激活失败：Cordis 会先打「N entry did not activate」，上一行通常是 <名> (<包>): <Error>: <消息>
_PLUGIN_ERR = re.compile(
    r"^\s*(?P<name>[\w@./-]+)\s+\((?P<pkg>[^)]+)\):\s+(?P<err>[A-Za-z]*Error|[A-Za-z]*Exception):\s+(?P<msg>.+)$")

_TOOL_CODE_NOTES = {
    "UNKNOWN_TOOL": "工具在会话中途消失（多为运行中改了 profile 或插件被卸载）",
    "FS_SANDBOX_DENIED": "被文件沙箱拒绝（工具想写但没权限）",
    "WEB_PROVIDER_ERROR": "联网工具失败（网络或供应商侧）",
}


def _scan_signals(r: Run) -> None:
    """扫事件里的环境类异常：日志签名 + 工具失败码。

    这些不是「崩溃」，但会让会话半残（工具消失、写入失败、shell 起不来），
    单列出来，避免统计上被当成一切正常。
    """
    seen: set[str] = set()
    msgs: list[str] = []
    for e in r.events:
        ev = e.get("event") or ""
        if ev == "execute.failed":
            code = (e.get("fields") or {}).get("failureCode")
            if code:
                r.tool_failures[code] = r.tool_failures.get(code, 0) + 1
            continue
        src = e.get("source") or ""
        if not (src.startswith("host") or src == "renderer"):
            continue
        msg = str(e.get("message") or "")
        if not msg:
            continue
        msgs.append(msg)
        for name, rx, _note in _LOG_SIGNATURES:
            if name not in seen and rx.search(msg):
                seen.add(name)

    # 端口冲突常来自「上一实例还没退干净」：本轮后来听成功了就只是短暂冲突
    if "port-in-use" in seen:
        recovered = any(("已监听" in m) or ("listening on" in m.lower()) for m in msgs)
        if recovered:
            seen.discard("port-in-use")
            seen.add("port-conflict-retried")

    # 插件没激活：从 stderr 的多行堆栈里认 <名> (<包>): <Error>: <消息>
    if any("did not activate" in m for m in msgs):
        for m in msgs:
            for ln in m.splitlines():
                hit = _PLUGIN_ERR.match(ln)
                if not hit:
                    continue
                rec = {"name": hit.group("name"), "package": hit.group("pkg"),
                       "error": f"{hit.group('err')}: {hit.group('msg')[:120]}"}
                if rec not in r.inactive_plugins:
                    r.inactive_plugins.append(rec)
        if "plugin-inactive" not in seen:
            seen.add("plugin-inactive")

    r.signals = sorted(seen)
    r.degraded = r.degraded or bool(seen & _DEGRADING) or bool(r.inactive_plugins)

    extra_notes = {"plugin-inactive": "有插件没激活成功（相关功能缺失，进程本身没死）",
                   "port-conflict-retried": "端口短暂冲突，本轮后来监听成功"}
    for name in r.signals:
        note = next((n for k, _rx, n in _LOG_SIGNATURES if k == name), "") or extra_notes.get(name, "")
        r.annotations.append(f"环境异常 {name}：{note}" if note else f"环境异常 {name}")

    if r.inactive_plugins:
        names = "、".join(f"{p['name']}（{p['error']}）" for p in r.inactive_plugins[:3])
        r.annotations.append(f"插件没激活：{names}")
        if any("without inject" in p["error"] for p in r.inactive_plugins):
            r.annotations.append(
                "插件没声明 inject：Cordis 的 ctx 是服务代理，客户端/宿主模块都要 "
                "exports.inject = [...], 否则取不到 ctx.tools / ctx.slots")

    for code, note in _TOOL_CODE_NOTES.items():
        n = r.tool_failures.get(code, 0)
        if n >= 3:
            r.annotations.append(f"工具失败 {n} 次 {code}：{note}")
            if code == "UNKNOWN_TOOL":
                r.degraded = True


def _grade_gone(r: Run) -> None:
    """子进程死亡 → 标记「应用还活着但能力受损」。

    注意：这里**不改 category**。category 只表示一次失败的原因，
    而渲染进程崩溃的 run 可能干净退出；混在一起会让统计失真。
    """
    types = {(g.get("type") or "").lower() for g in r.gone}
    reasons = {(g.get("reason") or "").lower() for g in r.gone}
    services = " ".join(str(g.get("service") or "") for g in r.gone).lower()

    if "renderer" in types or "gpu" in types:
        r.degraded = True
        r.degradation.append("renderer")
        r.annotations.append(
            "渲染进程/GPU 进程崩过（type=" + ",".join(sorted(types)) +
            " reason=" + ",".join(sorted(reasons)) + "），界面可能白过；建议重启应用")
    if "oom" in reasons:
        r.degraded = True
        if "oom" not in r.degradation:
            r.degradation.append("oom")
        r.annotations.append("子进程 OOM（内存不足），可能被系统杀掉")
    if "networkservice" in services or "network.mojom" in services:
        r.degraded = True
        if "network" not in r.degradation:
            r.degradation.append("network")
        r.annotations.append("网络服务子进程崩过，网络请求会短暂失败")


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
        _grade_gone(r)
        _scan_signals(r)
    _mark_crash_loops(ordered)
    return runs


def _mark_crash_loops(ordered: list[Run]) -> None:
    """10 分钟内连续 ≥3 次需要处理的结局 → 标 crash_loop（反复崩，不是偶发）。"""
    problems = [r for r in ordered
                if r.kind in {"crash", "boot-fail", "unclean-unknown"} and r.start]
    for i, r in enumerate(problems):
        window = [p for p in problems[i:]
                  if p.start and (p.start - r.start).total_seconds() <= 600]
        if len(window) >= 3:
            r.crash_loop = True
            r.annotations.append(
                f"连环崩：10 分钟内 {len(window)} 次异常启动（第一次 {r.start.strftime('%H:%M')}）")


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
        "degraded": sum(1 for r in runs.values() if r.degraded),
        "crash_loops": sum(1 for r in runs.values() if r.crash_loop),
        "signals": dict(Counter(
            name for r in runs.values() for name in r.signals)),
        "inactive_plugins": sorted({p["name"] for r in runs.values()
                                    for p in r.inactive_plugins}),
        "tool_failures": dict(Counter(
            code for r in runs.values() for code, n in r.tool_failures.items() for _ in range(n))),
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
                "annotations": r.annotations,
                "degraded": r.degraded,
                "crash_loop": r.crash_loop,
                "gone": r.gone,
                "degradation": r.degradation,
                "signals": r.signals,
                "tool_failures": r.tool_failures,
                "inactive_plugins": r.inactive_plugins,
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
                    "category", "repair", "boot", "shutdown", "host",
                    "degraded", "crash_loop"])
        for name, runs in groups.items():
            for rid, r in sorted(
                    runs.items(),
                    key=lambda kv: kv[1].start or datetime.min.replace(tzinfo=timezone.utc)):
                w.writerow([name, rid,
                            r.start.isoformat() if r.start else "",
                            round(r.duration, 1), r.kind, r.category or "",
                            r.repair, r.gate_boot, r.gate_shutdown, r.gate_host,
                            r.degraded, r.crash_loop])
        return 0

    for name, runs in groups.items():
        s = summarize(runs)
        label = f"[{name}] " if len(groups) > 1 else ""
        print(f"{label}run 总数 {s['runs']}  需要处理 {s['real_problems']}  "
              f"可由配置恢复 {s['recoverable_by_config']}")
        print(f"{label}结局分布: {s['kinds']}")
        print(f"{label}崩因分布: {s['categories']}")
        if s["degraded"] or s["crash_loops"]:
            print(f"{label}另有: 能力受损 {s['degraded']} 次，连环崩 {s['crash_loops']} 处")
        if s.get("inactive_plugins"):
            print(f"{label}插件未激活: {', '.join(s['inactive_plugins'])}")
        if s.get("signals"):
            top = ", ".join(f"{k}={v}" for k, v in
                            sorted(s["signals"].items(), key=lambda x: -x[1])[:4])
            print(f"{label}环境异常: {top}")
        if "--list" in argv:
            for rid, r in sorted(
                    runs.items(),
                    key=lambda kv: kv[1].start or datetime.min.replace(tzinfo=timezone.utc)):
                stamp = r.start.strftime("%m-%d %H:%M") if r.start else "?"
                note = "  ← " + "；".join(r.annotations) if r.annotations else ""
                print(f"  {stamp} {rid[:8]} {r.kind:16s} {str(r.category or '-'):14s} "
                      f"dur={r.duration:7.1f}s repair={r.repair}{note}")
        if len(groups) > 1:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
