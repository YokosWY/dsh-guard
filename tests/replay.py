#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回放：拿 8 天历史 run 检验 detect.py。

说明（别把自己的测试想得太美）：
  tests/expected.json 由 tests/build_expected.py 用一份「独立写的评分规则」生成，
  而 detect.py 也按同一条因果规则（关机事件发生在 run 结束→下次启动之间）实现。
  所以两边一致只证明「实现没跑偏」，**不证明规则本身对**。
  规则对不对看两件事：① 下面每条 problem run 的人工核对记录；② P1 起的实盘影子运行。

用法:
  python tests/replay.py              # 打印矩阵 + 逐条 problem 审计，写 reports/replay-*.md
  python tests/replay.py --quiet      # 只返回码
退出码: 0 全对；1 有漏报/误报；2 数据缺失
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import detect as D  # noqa: E402

EXPECTED = ROOT / "tests" / "expected.json"
REPORTS = ROOT / "reports"

FAMILY = {
    "ok": "clean", "in-progress": "clean", "shutdown": "clean", "quit-forced": "clean",
    "boot-fail": "problem", "crash": "problem", "unclean-unknown": "unknown",
}


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not EXPECTED.exists():
        print("缺少 tests/expected.json，先跑 python tests/build_expected.py")
        return 2
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    runs = D.analyse()
    now = datetime.now(timezone.utc)

    missing = [rid for rid in runs if rid not in expected]
    if missing:
        print(f"⚠ {len(missing)} 个 run 不在 expected.json 里（日志有新增？请重跑 build_expected）")

    rows = []
    for rid, r in runs.items():
        exp = expected.get(rid)
        if not exp:
            continue
        exp_kind = exp["kind"]
        rows.append({
            "run": rid[:8],
            "start": r.start.strftime("%m-%d %H:%M"),
            "detect": r.kind,
            "expect": exp_kind,
            "category": r.category or "",
            "exp_category": exp.get("category") or "",
            "duration": f"{r.duration:.1f}s",
            "repair": r.repair,
            "note": exp.get("note", ""),
            "same_kind": r.kind == exp_kind,
            "same_family": FAMILY.get(r.kind) == FAMILY.get(exp_kind),
            "same_cat": (r.category or None) == (exp.get("category") or None),
        })
    rows.sort(key=lambda x: x["start"])

    kinds = Counter(r["detect"] for r in rows)
    fam = Counter(FAMILY.get(r["detect"], "?") for r in rows)
    miss = [r for r in rows if r["same_kind"] is False
            and FAMILY.get(r["expect"]) == "problem" and FAMILY.get(r["detect"]) != "problem"]
    false_alarm = [r for r in rows if r["same_kind"] is False
                   and FAMILY.get(r["expect"]) == "clean" and FAMILY.get(r["detect"]) == "problem"]
    kind_mismatch = [r for r in rows if not r["same_kind"]]
    cat_mismatch = [r for r in rows if r["same_family"] and not r["same_cat"]]
    problems = [r for r in rows if FAMILY.get(r["detect"]) == "problem"]
    unknowns = [r for r in rows if FAMILY.get(r["detect"]) == "unknown"]
    quit_forced = [r for r in rows if r["detect"] == "quit-forced"]

    total = len(rows)
    acc_family = sum(r["same_family"] for r in rows) / total if total else 0
    acc_kind = sum(r["same_kind"] for r in rows) / total if total else 0

    lines = []
    lines.append(f"# 回放报告 {now.astimezone().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(f"- 样本：{total} 个 run（跳过的：{len(runs) - total}）")
    lines.append(f"- 结局分布：{dict(kinds)}")
    lines.append(f"- 三分类：{dict(fam)}")
    lines.append(f"- 与期望标签：完全一致 {acc_kind:.1%}，三分类一致 {acc_family:.1%}")
    lines.append(f"- 漏报（期望崩、判成干净）：{len(miss)}　误报（期望干净、判成崩）：{len(false_alarm)}")
    lines.append(f"- 类目不一致（同属 problem/clean 但 category 不同）：{len(cat_mismatch)}")
    lines.append("")
    lines.append("## 需要人看的 run")
    lines.append("")
    lines.append("| 起始 | run | 判定 | 类目 | 时长 | 建议 | 依据 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in problems + unknowns + quit_forced:
        lines.append(f"| {r['start']} | {r['run']} | {r['detect']} | {r['category'] or '-'} | "
                     f"{r['duration']} | {r['repair']} | {r['note'][:60]} |")
    lines.append("")
    lines.append("## 口径说明")
    lines.append("")
    lines.append("- `shutdown`：机器关机/睡眠终止，不算崩溃（8 天里占大头，别当成事故）")
    lines.append("- `quit-forced`：用户关窗但 host 卡死 >5s 被强杀，是关闭路径缺陷，不是崩溃")
    lines.append("- `boot-fail`：没到 Host ready 就失败。`shell-load` 类 profile 根本没加载，与配置无关")
    lines.append("- `crash`：到过 Host ready 后失败")
    lines.append("- `unclean-unknown`：无 run.end、无 runtime.failed、无关机事件 —— 交人工，不猜")
    lines.append("")

    report = "\n".join(lines)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"replay-{now.astimezone().strftime('%Y%m%d-%H%M')}.md"
    out.write_text(report + "\n", encoding="utf-8")
    # 只留最近 10 份，别让报告目录长大
    for old in sorted(REPORTS.glob("replay-*.md"))[:-10]:
        old.unlink(missing_ok=True)

    if "--quiet" not in argv:
        print(report)
        print(f"\n报告已写 {out}")
    return 0 if (not miss and not false_alarm) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
