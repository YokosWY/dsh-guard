#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""救援流程端到端演练：造坏配置 → rescue --apply → 还原 → 退得回来。

全程在临时目录里，不碰真配置，也不真启动应用。

  python tests/test_rescue_e2e.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import lkg  # noqa: E402
import preflight  # noqa: E402
import rescue  # noqa: E402


class RescueE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dshguard-e2e-"))
        home = self.tmp / "home"
        prof = home / "profiles" / "desktop"
        prof.mkdir(parents=True)
        (prof / "node_modules").mkdir()
        (prof / "package.json").write_text(json.dumps({
            "dependencies": {}, "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}
        }), encoding="utf-8")
        (prof / "cordis.patch.yml").write_text("- id: ui-settings\n  disabled: true\n",
                                               encoding="utf-8")
        (home / "storages").mkdir()
        (home / "storages" / "clarify-tier.json").write_text('{"mode":"high"}', encoding="utf-8")
        self.home, self.prof = home, prof

        self.cfg = {
            "home": str(home), "profile": "desktop", "keep": 5,
            "items": [
                {"bucket": "profiles", "path": "profiles/desktop/package.json", "mode": "restore"},
                {"bucket": "profiles", "path": "profiles/desktop/cordis.patch.yml", "mode": "restore"},
                {"bucket": "storages", "path": "storages/clarify-tier.json", "mode": "restore"},
            ],
        }
        self._orig = (lkg.HOME, lkg.LKG_DIR, lkg.QUARANTINE, lkg.LOGS, lkg.BASELINE,
                      lkg.load_config, lkg._dsh_processes, lkg.MTIME_QUIET_SECONDS,
                      preflight.HOME, preflight.PROFILE, rescue.APP_EXE, rescue.APP_CWD)
        lkg.HOME = home
        lkg.LKG_DIR = self.tmp / "lkg"
        lkg.QUARANTINE = self.tmp / "quarantine"
        lkg.LOGS = self.tmp / "logs"
        lkg.BASELINE = lkg.LKG_DIR / "baseline.json"
        lkg.load_config = lambda: json.loads(json.dumps(self.cfg))
        lkg._dsh_processes = lambda: []
        lkg.MTIME_QUIET_SECONDS = 0
        lkg.LKG_DIR.mkdir(parents=True, exist_ok=True)
        preflight.HOME = home
        preflight.PROFILE = prof
        preflight.results.clear()
        # 伪 exe：只验证命令拼装，不真启动
        rescue.APP_EXE = self.tmp / "fake" / "DSH NEXT.exe"
        rescue.APP_CWD = self.tmp / "fake"
        (self.tmp / "fake").mkdir()
        rescue.APP_EXE.write_bytes(b"")
        self.launched = []
        self._orig_popen = rescue.subprocess.Popen
        rescue.subprocess.Popen = lambda args, **kw: self.launched.append(args)

    def tearDown(self):
        (lkg.HOME, lkg.LKG_DIR, lkg.QUARANTINE, lkg.LOGS, lkg.BASELINE,
         lkg.load_config, lkg._dsh_processes, lkg.MTIME_QUIET_SECONDS,
         preflight.HOME, preflight.PROFILE, rescue.APP_EXE, rescue.APP_CWD) = self._orig
        rescue.subprocess.Popen = self._orig_popen
        preflight.results.clear()

    def make_baseline(self):
        m = lkg.snapshot("baseline", cfg=self.cfg, quiet=True)
        lkg.BASELINE.write_text(json.dumps({
            "id": m["id"], "created": m["created"], "file_count": m["file_count"],
            "promoted_at": "test", "note": "e2e"}), encoding="utf-8")
        return m

    def test_rescue_restores_bom_broken_config(self):
        base = self.make_baseline()
        # 造故障：package.json 被写进 BOM，cordis.patch.yml 被删
        (self.prof / "package.json").write_bytes(
            b"\xef\xbb\xbf" + (self.prof / "package.json").read_bytes())
        (self.prof / "cordis.patch.yml").unlink()

        rc = rescue.main(["--apply", "--yes"])
        self.assertEqual(rc, 0)
        # 文件回来了
        self.assertTrue((self.prof / "cordis.patch.yml").exists())
        self.assertFalse((self.prof / "package.json").read_bytes().startswith(b"\xef\xbb\xbf"))
        # 坏的那份留档了（pre-restore 快照 + quarantine 两份证据）
        self.assertTrue(any("pre-restore" in json.dumps(m) for m in lkg.list_snapshots()))
        self.assertTrue(list(lkg.QUARANTINE.glob("*/profiles/desktop/package.json")))
        # 还原后再体检应该没有阻塞问题
        preflight.results.clear()
        rc2 = preflight.run(do_drift=True)
        self.assertLess(rc2, 2)

    def test_rescue_refuses_when_app_running(self):
        self.make_baseline()
        (self.prof / "cordis.patch.yml").unlink()
        lkg._dsh_processes = lambda: ["999:DSH NEXT.exe"]
        rc = rescue.main(["--apply", "--yes"])
        self.assertEqual(rc, 3)
        self.assertFalse((self.prof / "cordis.patch.yml").exists())

    def test_safe_mode_launch_args(self):
        rc = rescue.main(["--safe-mode"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.launched), 1)
        self.assertIn("--next-safe-mode", self.launched[0])

    def test_normal_launch_has_no_safe_flag(self):
        rc = rescue.main(["--launch"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.launched[0][1:], [])

    def test_safe_mode_blocked_when_app_running(self):
        lkg._dsh_processes = lambda: ["999:DSH NEXT.exe"]
        rc = rescue.main(["--safe-mode"])
        self.assertEqual(rc, 3)
        self.assertEqual(self.launched, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
