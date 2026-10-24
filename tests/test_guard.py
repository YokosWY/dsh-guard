#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""guard.py 的单测：首次建基线、新事故只报一次、关闭时不扫描。

  python tests/test_guard.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import detect  # noqa: E402
import guard  # noqa: E402
import lkg  # noqa: E402


def make_run(rid: str, kind: str, category: str | None, start: datetime) -> detect.Run:
    r = detect.Run(run_id=rid, events=[])
    r.start = start
    r.end = start + timedelta(seconds=3)
    r.kind = kind
    r.category = category
    r.repair = "safe-mode"
    r.evidence = ["fake evidence"]
    return r


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dshguard-guard-"))
        self._orig = (guard.STATE_DIR, guard.CONTROL, guard.HEARTBEAT, guard.SCAN,
                      guard.LOGS, guard.GUARD_LOG, guard.NOTICE_LOCK,
                      lkg.LOGS, lkg._log_incident, guard.notify, detect.analyse)
        guard.STATE_DIR = self.tmp / "state"
        guard.CONTROL = guard.STATE_DIR / "control.json"
        guard.HEARTBEAT = guard.STATE_DIR / "heartbeat.json"
        guard.SCAN = guard.STATE_DIR / "scan.json"
        guard.LOGS = self.tmp / "logs"
        guard.GUARD_LOG = guard.LOGS / "guard.log"
        guard.NOTICE_LOCK = guard.STATE_DIR / ".notify-lock"
        guard.STATE_DIR.mkdir(parents=True)
        guard.LOGS.mkdir(parents=True)
        lkg.LOGS = self.tmp / "logs"
        self.notified = []
        guard.notify = lambda title, body: self.notified.append((title, body))
        self.t0 = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
        self.runs = {}
        detect.analyse = lambda now=None, home=None: dict(self.runs)
        self._orig_targets = guard.paths.targets
        guard.paths.targets = lambda: [{"name": "t", "home": self.tmp / "home"}]

    def tearDown(self):
        (guard.STATE_DIR, guard.CONTROL, guard.HEARTBEAT, guard.SCAN,
         guard.LOGS, guard.GUARD_LOG, guard.NOTICE_LOCK,
         lkg.LOGS, lkg._log_incident, guard.notify, detect.analyse) = self._orig
        guard.paths.targets = self._orig_targets

    def incident_lines(self):
        p = guard.LOGS / "incidents.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_first_run_seeds_without_notifying(self):
        self.runs = {
            "a": make_run("a", "crash", "plugin-import", self.t0),
            "b": make_run("b", "ok", None, self.t0 - timedelta(hours=1)),
        }
        out = guard.scan_once()
        self.assertEqual(out["seeded"], 1)
        self.assertEqual(out["new_problems"], 0)
        self.assertEqual(self.notified, [])
        self.assertEqual(self.incident_lines(), [])

    def test_new_incident_reported_once(self):
        self.runs = {"b": make_run("b", "ok", None, self.t0 - timedelta(hours=1))}
        guard.scan_once()                       # 建基线
        self.runs["c"] = make_run("c", "crash", "config-broken", self.t0)
        out = guard.scan_once()
        self.assertEqual(out["new_problems"], 1)
        self.assertEqual(len(self.notified), 1)
        self.assertIn("DSH 出事", self.notified[0][0])
        self.assertEqual(len(self.incident_lines()), 1)
        # 再扫一次不应重复报
        out2 = guard.scan_once()
        self.assertEqual(out2["new_problems"], 0)
        self.assertEqual(len(self.incident_lines()), 1)

    def test_clean_run_not_reported(self):
        self.runs = {"b": make_run("b", "ok", None, self.t0)}
        guard.scan_once()
        self.runs["d"] = make_run("d", "shutdown", None, self.t0 + timedelta(minutes=5))
        self.runs["e"] = make_run("e", "quit-forced", None, self.t0 + timedelta(minutes=6))
        out = guard.scan_once()
        self.assertEqual(out["new_problems"], 0)
        self.assertEqual(self.notified, [])

    def test_enabled_flag(self):
        guard.CONTROL.write_text(json.dumps({"enabled": False, "reason": "我在排查"}),
                                 encoding="utf-8")
        ok, why = guard.enabled()
        self.assertFalse(ok)
        self.assertEqual(why, "我在排查")
        guard.CONTROL.unlink()
        self.assertTrue(guard.enabled()[0])      # 没有控制文件默认开启

    def test_dedup_cap(self):
        self.runs = {"b": make_run("b", "ok", None, self.t0)}
        guard.scan_once()
        for i in range(450):
            self.runs[f"x{i}"] = make_run(f"x{i}", "ok", None, self.t0)
        guard.scan_once()
        seen = json.loads(guard.SCAN.read_text(encoding="utf-8"))["targets"]["t"]["seen"]
        self.assertLessEqual(len(seen), 400)

    def test_single_instance_detected(self):
        guard.write_json(guard.HEARTBEAT, {"pid": 999999, "ts": guard.utcnow().isoformat()})
        # 心跳新鲜但 pid 不存在 → 不该认为有实例
        self.assertIsNone(guard._other_instance_running())
        # 心跳新鲜且 pid 存在（用自己）→ 也不算（自己不算别人）
        guard.write_json(guard.HEARTBEAT, {"pid": guard.os.getpid(), "ts": guard.utcnow().isoformat()})
        self.assertIsNone(guard._other_instance_running())
        # 自己的 pid 但写成别的存活进程（当前测试进程的父进程）
        guard.write_json(guard.HEARTBEAT, {"pid": guard.os.getppid(), "ts": guard.utcnow().isoformat()})
        self.assertEqual(guard._other_instance_running(), guard.os.getppid())
        # 心跳过期 → 认为已经死了
        import os as _os
        import time as _time
        old = _time.time() - 600
        _os.utime(guard.HEARTBEAT, (old, old))
        self.assertIsNone(guard._other_instance_running())

    def test_evidence_captured_for_unclean(self):
        """unclean-unknown 必须留现场：事件尾巴 + 文本日志 + 进程快照。"""
        import detect as D
        logdir = self.tmp / "logs"
        logdir.mkdir(exist_ok=True)
        (logdir / "desktop-next.log").write_text(
            "\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        orig_logdir = D.LOG_DIR
        D.LOG_DIR = logdir
        try:
            r = make_run("deadbeef", "unclean-unknown", "silent", self.t0)
            r.gate_boot, r.gate_shutdown, r.gate_host = True, True, False
            path = guard.capture_evidence("deadbeef-0000", r)
            self.assertIsNotNone(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("unclean-unknown", text)
            self.assertIn("## 该 run 最后 20 条事件", text)
            self.assertIn("line99", text)          # 文本日志尾巴
            self.assertIn("## 当前进程快照", text)
        finally:
            D.LOG_DIR = orig_logdir

    def test_status_and_report(self):
        """--status 打印摘要；--report 另存 markdown。"""
        orig = (guard.REPORTS, guard.paths.targets)
        guard.REPORTS = self.tmp / "reports"
        try:
            self.assertEqual(guard.status(emit_report=False), 0)
            self.assertEqual(guard.status(emit_report=True), 0)
            files = list(guard.REPORTS.glob("status-*.md"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text(encoding="utf-8")
            self.assertIn("dsh-guard 状态", text)
            self.assertIn("| 目标 |", text)
        finally:
            guard.REPORTS, guard.paths.targets = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
