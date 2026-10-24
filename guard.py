#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-guard · 常驻守护（P2）

它只做监测和记录，**不自动改配置、不自动杀进程、不自动重启**——那是你审批才做的事。
界面上的「防崩守护」开关（插件 dsh-guard-ui）就是改 `state/control.json` 的 enabled。

读：`D:\\AI\\DSH-NEXT\\logs\\*.jsonl`（经 detect.py）、`state/control.json`
写：`state/heartbeat.json`（每 30 秒）、`logs/incidents.jsonl`（新事故）、`logs/guard.log`

用法:
  pythonw guard.py            # 常驻（HKCU Run 用这个）
  python guard.py --once      # 只扫一轮，打印结果，退出
  python guard.py --interval 120
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

import lkg  # noqa: E402  复用原子写 / 事故日志

try:  # 输出被重定向时 Windows 用 cp936，中文/emoji 会炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STATE_DIR = ROOT / "state"
CONTROL = STATE_DIR / "control.json"
HEARTBEAT = STATE_DIR / "heartbeat.json"
SCAN = STATE_DIR / "scan.json"
LOGS = ROOT / "logs"
GUARD_LOG = LOGS / "guard.log"
NOTICE_LOCK = STATE_DIR / ".notify-lock"

DEFAULT_INTERVAL = 120
HEARTBEAT_EVERY = 30
PROBLEM_KINDS = {"crash", "boot-fail", "unclean-unknown"}
NOTIFY_COOLDOWN = 600  # 秒：同一类事故最多 10 分钟弹一次

PYTHONW = paths.pythonw()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with tmp.open("rb+") as f:
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def log(msg: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"{utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    try:
        with GUARD_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def enabled() -> tuple[bool, str]:
    data = read_json(CONTROL, {})
    if not data:
        return True, ""          # 没有控制文件时默认开启（首次安装即保护）
    val = data.get("enabled", True)
    return bool(val), str(data.get("reason") or "")


def beat(extra: dict | None = None) -> None:
    payload = {"pid": os.getpid(), "ts": utcnow().isoformat(timespec="seconds")}
    if extra:
        payload.update(extra)
    write_json(HEARTBEAT, payload)


def notify(title: str, body: str) -> None:
    """气泡通知：另起一个 PowerShell，8 秒后自己退出，不阻塞守护。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        if NOTICE_LOCK.exists() and now - NOTICE_LOCK.stat().st_mtime < NOTIFY_COOLDOWN:
            return
        NOTICE_LOCK.write_text(str(now), encoding="utf-8")
    except OSError:
        return
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Warning;"
        f"$n.BalloonTipTitle={json.dumps(title, ensure_ascii=False)};"
        f"$n.BalloonTipText={json.dumps(body, ensure_ascii=False)};"
        "$n.Visible=$true;$n.ShowBalloonTip(6000);Start-Sleep -Seconds 8;$n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log(f"通知失败：{e}")


def _load_scan_state() -> dict:
    """扫描状态按 target 分开存；兼容旧格式（顶层 last_end/seen）。"""
    raw = read_json(SCAN, {})
    if isinstance(raw.get("targets"), dict):
        return raw
    legacy = {"last_end": raw.get("last_end") or "", "seen": raw.get("seen") or []}
    return {"version": 2, "targets": {"default": legacy} if legacy["last_end"] else {}}


def scan_once(notify_enabled: bool = True) -> dict:
    """扫一轮：逐个 DSH 目标，找出「上次扫过之后」的新问题 run。

    首次跑某个目标（状态里没有它）只建立历史基线，不逐条通知 —— 否则一装完就会
    把历史事故全弹一遍，纯噪音。
    """
    import detect

    state = _load_scan_state()
    fresh_all = []
    seeded_all = 0
    totals = 0

    for t in paths.targets():
        name, home = t["name"], t["home"]
        runs = detect.analyse(home=home)
        totals += len(runs)
        st = state["targets"].get(name) or {}
        first_run = not st or not st.get("last_end")
        seen = set(st.get("seen") or [])
        cutoff = None
        if st.get("last_end"):
            try:
                cutoff = datetime.fromisoformat(st["last_end"])
            except ValueError:
                cutoff = None

        if first_run:
            problems = [r for r in runs.values() if r.kind in PROBLEM_KINDS]
            seeded_all += len(problems)
            newest = max([r.end for r in runs.values()], default=None) if runs else None
            state["targets"][name] = {
                "last_end": (newest or utcnow()).isoformat(timespec="seconds"),
                "seen": sorted(runs.keys())[-400:],
                "scanned_at": utcnow().isoformat(timespec="seconds"),
                "runs": len(runs),
                "new_problems": 0,
                "seeded": len(problems),
            }
            log(f"[{name}] 首次运行：建立历史基线（{len(runs)} 次启动、{len(problems)} 条历史事故），不通知")
            continue

        fresh = []
        for rid, r in runs.items():
            if rid in seen or r.kind not in PROBLEM_KINDS:
                continue
            if cutoff and r.end <= cutoff:
                continue
            fresh.append((r.end, rid, r))
        fresh.sort(key=lambda x: x[0])

        for _end, rid, r in fresh:
            rec = {
                "t": utcnow().isoformat(timespec="seconds"),
                "kind": r.kind,
                "category": r.category,
                "home": name,
                "run": rid[:8],
                "started": r.start.isoformat() if r.start else None,
                "duration_s": round(r.duration, 1),
                "repair": r.repair,
                "detail": (r.evidence[0] if r.evidence else "")[:300],
            }
            if r.kind == "unclean-unknown":
                ev = capture_evidence(rid, r, name)
                if ev:
                    rec["evidence"] = str(ev)
            lkg._log_incident(f"detected:{r.kind}", rid[:8],
                              f"[{name}] {rec['detail']}", [r.category or "-"])
            log(f"[{name}] 新事故 {r.kind}/{r.category} run={rid[:8]} dur={r.duration:.1f}s"
                + (f"；现场存 {rec['evidence']}" if rec.get("evidence") else ""))
            fresh_all.append((name, rid, r))

        newest_end = max([r.end for r in runs.values()], default=None) if runs else None
        state["targets"][name] = {
            "last_end": (newest_end or utcnow()).isoformat(timespec="seconds"),
            "seen": sorted(seen | set(runs.keys()))[-400:],
            "scanned_at": utcnow().isoformat(timespec="seconds"),
            "runs": len(runs),
            "new_problems": len(fresh),
        }

    write_json(SCAN, state)

    if fresh_all and notify_enabled:
        name, rid, r = fresh_all[-1]
        hint = {
            "shell-load": "壳页没起来（profile 没加载）。完全退出 DSH 后跑 rescue.cmd --safe-mode。",
            "plugin-import": "插件加载失败。完全退出后跑 rescue.cmd --apply 从基线还原。",
            "config-broken": "配置被写坏（多半是 BOM）。完全退出后跑 rescue.cmd --apply。",
            "host-exit": "后端进程退出了。先 rescue.cmd --safe-mode 进去看看。",
            "silent": "没有证据的死法，已记录，等你人工看。",
        }.get(r.category or "", "已记录，等你人工看。")
        notify(f"DSH 出事：{r.kind}/{r.category or '未知'}（{name}）",
               f"{r.start.strftime('%m-%d %H:%M') if r.start else ''} 这次启动异常，"
               + f"持续 {r.duration:.0f}s。" + "\n" + hint)

    return {"seeded": seeded_all, "new_problems": len(fresh_all), "runs": totals,
            "fresh": [{"home": n, "run": rid[:8], "kind": r.kind,
                       "category": r.category, "dur": round(r.duration, 1)}
                      for n, rid, r in fresh_all]}


def _other_instance_running() -> int | None:
    """已有守护在跑就返回它的 pid，避免 HKCU Run 和界面按钮各起一个。"""
    beat = read_json(HEARTBEAT, {})
    pid = beat.get("pid")
    if not isinstance(pid, int) or pid == os.getpid():
        return None
    try:
        age = time.time() - HEARTBEAT.stat().st_mtime
    except OSError:
        return None
    if age > 90:                     # 心跳过期，认为已经死了
        return None
    try:
        import psutil  # type: ignore
        if psutil.pid_exists(pid):
            return pid
    except Exception:
        pass
    return None


def capture_evidence(rid: str, r, target_name: str = "default") -> Path | None:
    """给「无 run.end、无失败、无关机」的 run 留现场。

    这类死法日志里什么都没留，只能靠周边证据（文本日志尾巴 + 当时的进程表）反推。
    写 logs/unclean-<target>-<run8>-<时间>.log，一条事故一个文件。
    """
    try:
        import detect
    except Exception:
        return None
    stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")
    out = LOGS / f"unclean-{target_name}-{rid[:8]}-{stamp}.log"
    log_dir = (Path(r.home) / "logs") if getattr(r, "home", "") else detect.LOG_DIR
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            f.write(f"# unclean-unknown 现场\n")
            f.write(f"target: {target_name}\n")
            f.write(f"home: {r.home or detect.HOME}\n")
            f.write(f"run: {rid}\n")
            f.write(f"start: {r.start.isoformat() if r.start else '?'}\n")
            f.write(f"end:   {r.end.isoformat() if r.end else '?'}\n")
            f.write(f"duration: {r.duration:.1f}s  events: {len(r.events)}\n")
            f.write(f"gates: boot={r.gate_boot} shutdown={r.gate_shutdown} "
                    f"host={r.gate_host}\n\n")

            f.write("## 该 run 最后 20 条事件（原始 JSONL）\n")
            for e in r.events[-20:]:
                try:
                    f.write(json.dumps(e, ensure_ascii=False)[:600] + "\n")
                except Exception:
                    continue

            f.write("\n## 文本日志尾部（desktop-next.log 最后 60 行）\n")
            text_log = log_dir / "desktop-next.log"
            try:
                lines = text_log.read_text(encoding="utf-8", errors="ignore").splitlines()
                f.write("\n".join(lines[-60:]) + "\n")
            except Exception as e:
                f.write(f"(读不到：{e})\n")

            f.write("\n## 当前进程快照\n")
            try:
                import psutil  # type: ignore
                for p in psutil.process_iter(["pid", "name", "create_time"]):
                    try:
                        name = (p.info.get("name") or "")
                        if "dsh" in name.lower() or "python" in name.lower():
                            ct = p.info.get("create_time")
                            when = (datetime.fromtimestamp(ct).strftime("%m-%d %H:%M:%S")
                                    if ct else "?")
                            f.write(f"{p.info['pid']:>6}  {name:<32} {when}\n")
                    except Exception:
                        continue
            except Exception as e:
                f.write(f"(psutil 不可用：{e})\n")
        return out
    except Exception as e:
        log(f"现场保存失败：{type(e).__name__}: {e}")
        return None


def main(argv: list[str]) -> int:
    interval = DEFAULT_INTERVAL
    if "--interval" in argv:
        try:
            interval = int(argv[argv.index("--interval") + 1])
        except (IndexError, ValueError):
            pass

    if "--once" in argv:
        ok, why = enabled()
        log(f"单次扫描 control.enabled={ok}{(' reason=' + why) if why else ''}")
        result = scan_once(notify_enabled=True)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0

    if "--ensure" in argv:
        # 给计划任务用：只在「开关开着、但守护不在」时补拉一个，其它情况什么都不做。
        ok, why = enabled()
        if not ok:
            log(f"--ensure: 开关是关的（{why or '用户关闭'}），不拉起")
            return 0
        other = _other_instance_running()
        if other:
            return 0
        exe = PYTHONW if PYTHONW.exists() else Path(sys.executable)
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(exe), str(ROOT / "guard.py")],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log(f"--ensure: 守护不在，已通过 cmd/start 补拉（{exe.name}）")
        except Exception as e:
            log(f"--ensure: 补拉失败 {type(e).__name__}: {e}")
            return 1
        return 0

    if "--force" not in argv:
        other = _other_instance_running()
        if other:
            log(f"已有守护在跑（pid={other}），本进程退出")
            return 0

    log(f"守护启动 pid={os.getpid()} interval={interval}s")
    beat()
    last_beat = time.time()
    last_scan = 0.0
    while True:
        try:
            now = time.time()
            if now - last_beat >= HEARTBEAT_EVERY:
                beat()
                last_beat = now
            ok, why = enabled()
            if not ok:
                # 关掉只是不扫，进程留着，心跳继续（界面能看到「已关闭」）
                if now - last_scan >= interval:
                    log(f"已关闭，本次不扫描（{why or '用户关闭'}）")
                    last_scan = now
                time.sleep(5)
                continue
            if now - last_scan >= interval:
                t0 = time.time()
                r = scan_once()
                last_scan = time.time()
                if r["new_problems"]:
                    log(f"扫描完成 {r['new_problems']} 条新事故（{time.time()-t0:.1f}s）")
                beat({"last_scan": utcnow().isoformat(timespec="seconds")})
                last_beat = time.time()
            time.sleep(5)
        except KeyboardInterrupt:
            log("收到中断，退出")
            return 0
        except Exception as e:  # 守护自己绝不退
            log(f"循环异常（继续跑）：{type(e).__name__}: {e}")
            time.sleep(15)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
