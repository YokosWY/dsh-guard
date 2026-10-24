#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lkg.py 的单测：全部在临时目录里跑，绝不碰真配置。

  python tests/test_lkg.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lkg  # noqa: E402


class LkgTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dshguard-test-"))
        home = self.tmp / "home"
        prof = home / "profiles" / "desktop"
        prof.mkdir(parents=True)
        (prof / "package.json").write_text('{"name":"x","dsh":{"profile":{"bundles":["a"]}}}',
                                           encoding="utf-8")
        (prof / "cordis.patch.yml").write_text("- id: a\n  disabled: true\n", encoding="utf-8")
        lp = home / "local-plugins" / "p1"
        lp.mkdir(parents=True)
        (lp / "index.js").write_text("export default 1\n", encoding="utf-8")
        (lp / "node_modules").mkdir()
        (lp / "node_modules" / "junk.js").write_text("skip me\n", encoding="utf-8")
        st = home / "storages"
        st.mkdir()
        (st / "clarify-tier.json").write_text('{"mode":"high"}', encoding="utf-8")

        self.cfg = {
            "home": str(home),
            "profile": "desktop",
            "keep": 3,
            "items": [
                {"bucket": "profiles", "path": "profiles/desktop/package.json", "mode": "restore"},
                {"bucket": "profiles", "path": "profiles/desktop/cordis.patch.yml", "mode": "restore"},
                {"bucket": "local-plugins", "path": "local-plugins", "mode": "restore", "tree": True},
                {"bucket": "storages", "path": "storages/clarify-tier.json", "mode": "restore"},
            ],
        }
        self.home = home
        # 重定向仓库目录，别写到真项目里
        self._orig = (lkg.LKG_DIR, lkg.QUARANTINE, lkg.LOGS, lkg.BASELINE,
                      lkg._dsh_processes, lkg.MTIME_QUIET_SECONDS)
        lkg.LKG_DIR = self.tmp / "lkg"
        lkg.QUARANTINE = self.tmp / "quarantine"
        lkg.LOGS = self.tmp / "logs"
        lkg.BASELINE = lkg.LKG_DIR / "baseline.json"
        lkg.MTIME_QUIET_SECONDS = 0
        lkg._dsh_processes = lambda: []   # 测试里假装 DSH 没在跑
        lkg.LKG_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        (lkg.LKG_DIR, lkg.QUARANTINE, lkg.LOGS, lkg.BASELINE,
         lkg._dsh_processes, lkg.MTIME_QUIET_SECONDS) = self._orig

    def snap(self, reason="test"):
        return lkg.snapshot(reason, cfg=self.cfg, quiet=True)

    def test_snapshot_excludes_node_modules(self):
        m = self.snap()
        paths = [f["path"] for f in m["files"]]
        self.assertIn("local-plugins/p1/index.js", paths)
        self.assertNotIn("local-plugins/p1/node_modules/junk.js", paths)
        self.assertEqual(m["file_count"], 4)

    def test_diff_detects_drift(self):
        m = self.snap()
        (self.home / "profiles/desktop/package.json").write_text('{"name":"changed"}',
                                                                encoding="utf-8")
        rows = lkg.diff_against_snapshot(m, cfg=self.cfg)
        drift = [r["path"] for r in rows if r["state"] == "drift"]
        self.assertEqual(drift, ["profiles/desktop/package.json"])

    def test_dry_run_does_not_write(self):
        m = self.snap()
        target = self.home / "profiles/desktop/package.json"
        target.write_text('{"name":"changed"}', encoding="utf-8")
        rc = lkg.restore(m["id"], apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"name":"changed"}')

    def test_apply_restores_and_quarantines(self):
        m = self.snap()
        pkg = self.home / "profiles/desktop/package.json"
        yml = self.home / "profiles/desktop/cordis.patch.yml"
        pkg.write_text('{"name":"changed"}', encoding="utf-8")
        yml.unlink()
        rc = lkg.restore(m["id"], apply=True)
        self.assertEqual(rc, 0)
        self.assertIn('"name":"x"', pkg.read_text(encoding="utf-8"))
        self.assertTrue(yml.exists())
        # 原文件进 quarantine
        q = list(lkg.QUARANTINE.glob("*/profiles/desktop/package.json"))
        self.assertEqual(len(q), 1)
        self.assertIn("changed", q[0].read_text(encoding="utf-8"))
        # 事故日志
        log = (lkg.LOGS / "incidents.jsonl").read_text(encoding="utf-8")
        self.assertIn("restore-ok", log)

    def test_apply_refuses_when_dsh_running(self):
        m = self.snap()
        (self.home / "profiles/desktop/package.json").write_text("{}", encoding="utf-8")
        lkg._dsh_processes = lambda: ["1234:DSH NEXT.exe"]
        rc = lkg.restore(m["id"], apply=True)
        self.assertEqual(rc, 3)

    def test_apply_rolls_back_on_verify_failure(self):
        m = self.snap()
        pkg = self.home / "profiles/desktop/package.json"
        pkg.write_text('{"name":"changed"}', encoding="utf-8")
        # 篡改快照内的副本，让写后校验必然失败
        bad = lkg.LKG_DIR / m["id"] / "files" / "profiles/desktop/package.json"
        bad.write_text('{"name":"corrupted"}', encoding="utf-8")
        rc = lkg.restore(m["id"], apply=True)
        self.assertEqual(rc, 4)
        # 原文件被回滚
        self.assertIn("changed", pkg.read_text(encoding="utf-8"))
        self.assertIn("restore-failed", (lkg.LOGS / "incidents.jsonl").read_text(encoding="utf-8"))

    def test_prune_keeps_count_and_baseline(self):
        ids = []
        for i in range(5):
            m = self.snap(reason=f"r{i}")
            ids.append(m["id"])
        # 健康门槛依赖真机日志，单测里替掉（它自己另有「回放」覆盖）
        orig = lkg._health_ok_for_promote
        lkg._health_ok_for_promote = lambda **kw: (True, "")
        try:
            lkg.promote(ids[-1], yes=True)
        finally:
            lkg._health_ok_for_promote = orig
        snaps = lkg.list_snapshots()
        self.assertLessEqual(len(snaps), 3)
        base = lkg.load_baseline()
        self.assertEqual(base["id"], ids[-1])
        self.assertTrue((lkg.LKG_DIR / base["id"] / "manifest.json").exists())

    def test_reference_mode_not_restored_by_default(self):
        ref = self.home / "storages/schedule.json"
        ref.write_text('{"tasks":[1]}', encoding="utf-8")
        self.cfg["items"].append(
            {"bucket": "storages", "path": "storages/schedule.json", "mode": "reference"})
        m = self.snap()
        ref.write_text('{"tasks":[2]}', encoding="utf-8")
        lkg.restore(m["id"], apply=True)
        self.assertEqual(ref.read_text(encoding="utf-8"), '{"tasks":[2]}')
        lkg.restore(m["id"], apply=True, include_reference=True)
        self.assertEqual(ref.read_text(encoding="utf-8"), '{"tasks":[1]}')

    def test_same_path_restore_wins(self):
        self.cfg["items"].append(
            {"bucket": "storages", "path": "storages/clarify-tier.json", "mode": "reference"})
        items = lkg.expand_items(self.cfg)
        hit = [i for i in items if i.rel == "storages/clarify-tier.json"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].mode, "restore")


if __name__ == "__main__":
    unittest.main(verbosity=2)
