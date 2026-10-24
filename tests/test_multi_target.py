#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多目标支持的单测：paths.targets 解析、detect 按 home 分析、lkg/sessions 的 label 分目录。

  python tests/test_multi_target.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import detect  # noqa: E402
import lkg  # noqa: E402
import paths  # noqa: E402
import sessions  # noqa: E402


class TargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dshguard-multi-"))
        self._orig = (paths.CONFIG_FILE, lkg.LKG_DIR, lkg.BASELINE, sessions.DEST)
        paths.CONFIG_FILE = self.tmp / "config.json"
        lkg.LKG_DIR = self.tmp / "lkg"
        lkg.BASELINE = lkg.LKG_DIR / "baseline.json"
        sessions.DEST = self.tmp / "sessions-backup"
        lkg.LKG_DIR.mkdir(parents=True)

    def tearDown(self):
        paths.CONFIG_FILE, lkg.LKG_DIR, lkg.BASELINE, sessions.DEST = self._orig

    def write_cfg(self, data: dict):
        paths.CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_targets_from_config(self):
        self.write_cfg({"targets": [
            {"name": "next", "home": r"D:\A"},
            {"name": "official", "home": r"D:\B"},
        ]})
        t = paths.targets()
        self.assertEqual([x["name"] for x in t], ["next", "official"])
        self.assertEqual(str(t[1]["home"]), r"D:\B")

    def test_targets_fallback_to_single_home(self):
        cfg = self.tmp / "cfg2.json"
        paths.CONFIG_FILE = cfg
        cfg.write_text(json.dumps({"home": str(self.tmp / "home")}), encoding="utf-8")
        t = paths.targets()
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["name"], "default")

    def test_analyse_reads_given_home(self):
        home = self.tmp / "home-a"
        (home / "logs").mkdir(parents=True)
        line = {"time": "2026-10-24T01:00:00Z", "runId": "r1", "event": "run.start", "pid": 1}
        (home / "logs" / "desktop-next-2026-10-24.0.jsonl").write_text(
            json.dumps(line) + "\n", encoding="utf-8")
        runs = detect.analyse(home=home)
        self.assertIn("r1", runs)
        self.assertEqual(runs["r1"].home, str(home))

    def test_lkg_label_separates_repos(self):
        home = self.tmp / "home"
        (home / "profiles" / "desktop").mkdir(parents=True)
        (home / "profiles" / "desktop" / "package.json").write_text("{}", encoding="utf-8")
        cfg = {"home": str(home), "keep": 5, "items": [
            {"bucket": "profiles", "path": "profiles/desktop/package.json", "mode": "restore"}]}
        m1 = lkg.snapshot("t", cfg=cfg, quiet=True, label="next")
        m2 = lkg.snapshot("t", cfg=cfg, quiet=True, label="official")
        self.assertTrue((lkg.LKG_DIR / "next" / m1["id"]).exists())
        self.assertTrue((lkg.LKG_DIR / "official" / m2["id"]).exists())
        self.assertEqual(len(lkg.list_snapshots("next")), 1)
        self.assertEqual(len(lkg.list_snapshots("official")), 1)

    def test_sessions_label_separates_repos(self):
        home = self.tmp / "h"
        (home / "sessions" / "ws").mkdir(parents=True)
        (home / "sessions" / "ws" / "a.jsonl").write_text("{}", encoding="utf-8")
        orig_src = sessions.SRC
        try:
            sessions.SRC = home / "sessions"
            m = sessions.snapshot(quiet=True, label="next")
            self.assertTrue((sessions.DEST / "next" / m["id"]).exists())
            self.assertEqual(len(sessions.list_backups("next")), 1)
        finally:
            sessions.SRC = orig_src

    def test_csv_export(self):
        import contextlib
        import io
        home = self.tmp / "home-csv"
        (home / "logs").mkdir(parents=True)
        line = {"time": "2026-10-24T01:00:00Z", "runId": "r1", "event": "run.start", "pid": 1}
        (home / "logs" / "desktop-next-2026-10-24.0.jsonl").write_text(
            json.dumps(line) + "\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = detect.main(["--home", str(home), "--csv"])
        self.assertEqual(rc, 0)
        out = buf.getvalue().strip().splitlines()
        self.assertTrue(out[0].startswith("target,run_id,start"))
        self.assertEqual(len(out), 2)

    def test_child_process_gone_marks_degraded(self):
        home = self.tmp / "home-gone"
        (home / "logs").mkdir(parents=True)
        base = {"time": "2026-10-24T01:00:00Z", "runId": "g1"}
        lines = [
            dict(base, event="run.start", pid=1, source="electron"),
            dict(base, event="host.start.start", pid=1, source="host"),
            dict(base, event="child-process-gone", source="electron",
                 fields={"type": "Renderer", "reason": "oom", "exitCode": -1}),
            dict(base, event="run.end", source="electron"),
        ]
        (home / "logs" / "desktop-next-2026-10-24.0.jsonl").write_text(
            "".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        runs = detect.analyse(home=home)
        r = runs["g1"]
        self.assertTrue(r.degraded)
        self.assertIn("renderer", r.degradation)
        # category 只表示失败原因；降级不写 category（否则统计会失真）
        self.assertIsNone(r.category)
        self.assertTrue(any("渲染进程" in a for a in r.annotations))

    def test_crash_loop_flagged(self):
        home = self.tmp / "home-loop"
        (home / "logs").mkdir(parents=True)
        rows = []
        for i in range(3):
            rid = f"c{i}"
            for ev in ("run.start", "runtime.failed"):
                rows.append({"time": f"2026-10-24T01:0{i}:00Z", "runId": rid,
                             "event": ev, "source": "electron",
                             "message": "failed: ERR_FAILED" if ev == "runtime.failed" else ""})
        (home / "logs" / "desktop-next-2026-10-24.0.jsonl").write_text(
            "".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
        runs = detect.analyse(home=home)
        loops = [r for r in runs.values() if r.crash_loop]
        self.assertTrue(loops)
        self.assertTrue(any("连环崩" in a for a in loops[0].annotations))

    def _run_with(self, rows):
        tag = abs(hash(json.dumps(rows, sort_keys=True, ensure_ascii=False))) % 100000
        home = self.tmp / f"home-sig-{tag}"
        (home / "logs").mkdir(parents=True)
        for i, x in enumerate(rows):
            x.setdefault("time", f"2026-10-24T02:0{i}:00Z")
            x.setdefault("runId", "s1")
        (home / "logs" / "desktop-next-2026-10-24.0.jsonl").write_text(
            "".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
        return detect.analyse(home=home)["s1"]

    def test_port_conflict_signal(self):
        r = self._run_with([
            {"event": "run.start", "source": "electron"},
            {"event": "log", "source": "host.kernel.x", "level": "error",
             "message": "启动失败 127.0.0.1:8326：listen EADDRINUSE: address already in use"},
            {"event": "run.end", "source": "electron"},
        ])
        self.assertIn("port-in-use", r.signals)
        self.assertTrue(r.degraded)

    def test_file_locked_signal(self):
        r = self._run_with([
            {"event": "run.start", "source": "electron"},
            {"event": "log", "source": "host.kernel.x", "level": "error",
             "message": "写入失败 EPERM: operation not permitted, rename 'x.json.271' -> 'x.json'"},
            {"event": "run.end", "source": "electron"},
        ])
        self.assertIn("file-locked", r.signals)
        self.assertTrue(r.degraded)

    def test_unknown_tool_storm(self):
        rows = [{"event": "run.start", "source": "electron"}]
        for _ in range(3):
            rows.append({"event": "execute.failed", "source": "host.tools",
                         "fields": {"tool": "edit", "failureCode": "UNKNOWN_TOOL"}})
        rows.append({"event": "run.end", "source": "electron"})
        r = self._run_with(rows)
        self.assertEqual(r.tool_failures.get("UNKNOWN_TOOL"), 3)
        self.assertTrue(any("UNKNOWN_TOOL" in a for a in r.annotations))
        self.assertTrue(r.degraded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
