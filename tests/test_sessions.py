#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sessions.py 的单测：增量硬链接、去重、还原拒绝。

  python tests/test_sessions.py
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
import sessions  # noqa: E402


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dshguard-sess-"))
        self.src = self.tmp / "sessions"
        (self.src / "--D-ws--").mkdir(parents=True)
        (self.src / "--D-ws--" / "a.jsonl").write_text('{"n":1}\n', encoding="utf-8")
        (self.src / "--D-ws--" / "b.jsonl").write_text('{"n":2}\n', encoding="utf-8")
        self._orig = (sessions.SRC, sessions.DEST, lkg.QUARANTINE, lkg.LOGS,
                      lkg._dsh_processes, lkg._log_incident)
        sessions.SRC = self.src
        sessions.DEST = self.tmp / "backup"
        lkg.QUARANTINE = self.tmp / "quarantine"
        lkg.LOGS = self.tmp / "logs"
        lkg._dsh_processes = lambda: []
        lkg._log_incident = lambda *a, **k: None

    def tearDown(self):
        (sessions.SRC, sessions.DEST, lkg.QUARANTINE, lkg.LOGS,
         lkg._dsh_processes, lkg._log_incident) = self._orig

    def test_incremental_uses_hardlinks(self):
        first = sessions.snapshot(quiet=True)
        second = sessions.snapshot(quiet=True)
        self.assertEqual(first["copied"], 2)
        self.assertEqual(second["copied"], 0)
        self.assertEqual(second["linked"], 2)
        self.assertEqual(second["bytes_new"], 0)

    def test_changed_file_is_copied(self):
        sessions.snapshot(quiet=True)
        p = self.src / "--D-ws--" / "a.jsonl"
        p.write_text('{"n":11}\n', encoding="utf-8")
        m = sessions.snapshot(quiet=True)
        self.assertEqual(m["copied"], 1)
        self.assertEqual(m["linked"], 1)

    def test_skip_tmp_files(self):
        (self.src / "x.tmp").write_text("junk", encoding="utf-8")
        m = sessions.snapshot(quiet=True)
        self.assertNotIn("x.tmp", [f["path"] for f in m["files"]])

    def test_restore_dry_run_then_apply(self):
        m = sessions.snapshot(quiet=True)
        p = self.src / "--D-ws--" / "a.jsonl"
        p.write_text('{"n":99}\n', encoding="utf-8")
        rc = sessions.restore(m["id"], apply=False)
        self.assertEqual(rc, 0)
        self.assertIn("99", p.read_text(encoding="utf-8"))
        rc = sessions.restore(m["id"], apply=True)
        self.assertEqual(rc, 0)
        self.assertIn("1", p.read_text(encoding="utf-8"))

    def test_restore_refused_when_running(self):
        m = sessions.snapshot(quiet=True)
        (self.src / "--D-ws--" / "a.jsonl").write_text('{"n":99}\n', encoding="utf-8")
        lkg._dsh_processes = lambda: ["1:DSH NEXT.exe"]
        rc = sessions.restore(m["id"], apply=True)
        self.assertEqual(rc, 3)

    def test_prune_keeps(self):
        for _ in range(10):
            sessions.snapshot(quiet=True)
        self.assertLessEqual(len(sessions.list_backups()), sessions.KEEP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
