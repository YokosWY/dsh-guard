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


if __name__ == "__main__":
    unittest.main(verbosity=2)
