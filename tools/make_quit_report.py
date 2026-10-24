#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成「关闭超时被强杀」的缺陷报告（给应用方）。

现象来自实测：用户正常关窗，`host.stop` 卡住超过 5 秒，应用打 `shutdown.timeout` 后强杀自己
（`run.forced-exit`），下一次启动报 `run.previous-unclean`，把一次正常关闭记成异常退出。

用法:
  python tools/make_quit_report.py            # 写 reports/bug-quit-forced.md
  python tools/make_quit_report.py --stdout
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import detect as D  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = ROOT / "reports" / "bug-quit-forced.md"
APP_VERSION = "2.0.18-next"


def events_of(r: D.Run, name: str) -> list[dict]:
    return [e for e in r.events if (e.get("event") or "") == name]


def main(argv: list[str]) -> int:
    runs = D.analyse()
    ordered = sorted(runs.values(), key=lambda r: r.start)
    cases = [r for r in ordered if r.kind == "quit-forced"]
    if not cases:
        print("没有 quit-forced 样本")
        return 1

    lines = [
        "# 缺陷报告：关窗时 host.stop 超时，被强杀并记成异常退出",
        "",
        f"- 版本：DSH NEXT `{APP_VERSION}`（`D:\\AI\\DSH-NEXT\\app\\DSH NEXT.exe`）",
        f"- 生成时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}",
        f"- 样本：{len(cases)} 次（取自 {ordered[0].start.date()} ~ {ordered[-1].start.date()}，"
        f"同期共 {len(ordered)} 次启动）",
        "",
        "## 现象",
        "",
        "用户正常关闭主窗口后，应用开始 `shutdown.start`；`host.stop` 阶段卡住超过 5 秒，"
        "日志出现 `Shutdown exceeded five seconds`（事件 `shutdown.timeout`），随后 `run.forced-exit`。",
        "下一次启动出现 `run.previous-unclean`，也就是把「正常关窗」记录成了一次异常退出。",
        "",
        "## 影响",
        "",
        "1. 每次正常退出都被计成 unpublished crash，用户看到「上次异常退出」类提示",
        "2. 与真实崩溃混在一起，排障时要靠日志逐条排除",
        "3. 强制结束进程时可能有未落盘的状态（本次样本里未观察到数据丢失，但风险存在）",
        "",
        "## 样本",
        "",
        "| # | 启动时刻 (UTC) | 会话时长 | shutdown.start → 强杀 | 之后下次启动报 unclean |",
        "|---|---|---|---|---|",
    ]

    for i, r in enumerate(cases, 1):
        start = events_of(r, "shutdown.start")
        timeout = events_of(r, "shutdown.timeout")
        forced = events_of(r, "run.forced-exit")
        delta = "-"
        if start and forced:
            t0 = D._utc(start[0]["time"])
            t1 = D._utc(forced[0]["time"])
            delta = f"{int((t1 - t0).total_seconds())}s"
        nxt = next((x for x in ordered if x.start > r.end), None)
        unclean = "是" if (nxt and any(e.get("event") == "run.previous-unclean"
                                        for e in nxt.events[:3])) else "否"
        dur = f"{r.duration/60:.0f} 分钟" if r.duration > 120 else f"{r.duration:.0f} 秒"
        lines.append(f"| {i} | {r.start.strftime('%m-%d %H:%M:%S')} | {dur} | {delta} | {unclean} |")

    lines += [
        "",
        "## 原始日志（首例）",
        "",
        "```json",
    ]
    sample = cases[0]
    for e in sample.events:
        if (e.get("event") or "") in ("shutdown.start", "shutdown.timeout", "run.forced-exit"):
            lines.append(
                f'{{"time":"{e["time"]}","event":"{e["event"]}","pid":{e.get("pid")}}}')
    lines += [
        "```",
        "",
        "## 复现步骤",
        "",
        "1. 打开 DSH NEXT，正常使用一段时间（与时长无关，短会话也出现）",
        "2. 点关闭窗口",
        f"3. 观察 `%APPDATA%\\..\\` 下 home 的 `logs\\desktop-next.log`：应出现 `Shutdown exceeded five seconds`",
        "4. 重新打开，观察日志出现 `run.previous-unclean`",
        "",
        "## 猜测方向（供参考，未验证）",
        "",
        "- `host.stop` 等待某个子进程（MCP 连接 / 子代理 / 终端会话）未按时退出",
        "- 每次强杀时日志出现 `pendingOperations: 2`，可能与未完成的工具调用有关",
        "",
        "## 附加材料",
        "",
        "- 判定口径：关窗导致的 `run.forced-exit` 不计为崩溃，已从崩溃统计里排除",
        "- 数据来源：`D:\\AI\\DSH-NEXT\\logs\\desktop-next-*.jsonl`（结构化事件）",
    ]

    text = "\n".join(lines) + "\n"
    if "--stdout" in argv:
        print(text)
        return 0
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"已写 {OUT}（{len(cases)} 个样本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
