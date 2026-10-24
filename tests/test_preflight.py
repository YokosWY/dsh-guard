#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""preflight.py 的单测：用临时目录造故障，不碰真配置。

  python tests/test_preflight.py
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
import preflight as P  # noqa: E402


def make_junction(link: Path, target: Path) -> None:
    """测试里造软链：Windows 普通账号没有 symlink 特权，用 junction。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    import _winapi  # Windows 专用，普通账号也能建目录 junction
    _winapi.CreateJunction(str(target), str(link))


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dshguard-pf-"))
        home = self.tmp / "home"
        prof = home / "profiles" / "desktop"
        prof.mkdir(parents=True)
        (prof / "node_modules").mkdir()
        (home / "storages").mkdir()
        self.home, self.prof = home, prof
        self._orig = (P.HOME, P.PROFILE, lkg.HOME, lkg.LKG_DIR, lkg.BASELINE)
        P.HOME = home
        P.PROFILE = prof
        lkg.HOME = home
        lkg.LKG_DIR = self.tmp / "lkg"
        lkg.BASELINE = lkg.LKG_DIR / "baseline.json"
        P.results.clear()

    def tearDown(self):
        P.HOME, P.PROFILE, lkg.HOME, lkg.LKG_DIR, lkg.BASELINE = self._orig
        P.results.clear()

    def codes(self):
        return [r["code"] for r in P.results if r["level"] == "FAIL"]

    def write_pkg(self, data: dict, bom: bool = False):
        txt = json.dumps(data, ensure_ascii=False)
        raw = ("\ufeff" + txt).encode("utf-8") if bom else txt.encode("utf-8")
        (self.prof / "package.json").write_bytes(raw)

    def test_bom_caught(self):
        self.write_pkg({"dsh": {"profile": {"bundles": []}}}, bom=True)
        P.check_json(self.prof / "package.json", required_key="dsh")
        self.assertIn("bom", self.codes())

    def test_broken_json_caught(self):
        (self.prof / "package.json").write_text('{"dsh":', encoding="utf-8")
        P.check_json(self.prof / "package.json", required_key="dsh")
        self.assertIn("json-parse", self.codes())

    def test_link_target_missing_caught(self):
        pkg = {
            "dependencies": {"@local/x": "link:D:/nope/not-here"},
            "dsh": {"profile": {"bundles": ["@local/x"]}},
        }
        self.write_pkg(pkg)
        P.check_bundles(pkg)
        self.assertIn("bundle-missing", self.codes())

    def test_local_link_ok_when_target_complete(self):
        target = self.tmp / "plug"
        target.mkdir()
        (target / "index.js").write_text("export default 1\n", encoding="utf-8")
        (target / "package.json").write_text('{"main":"index.js"}', encoding="utf-8")
        # 真实环境里 @local 插件在 node_modules 下有软链，DSH 从这里解析 bundle
        make_junction(self.prof / "node_modules" / "@local" / "x", target)
        pkg = {"dependencies": {"@local/x": f"link:{target.as_posix()}"},
               "dsh": {"profile": {"bundles": ["@local/x"]}}}
        self.write_pkg(pkg)
        P.check_bundles(pkg)
        self.assertNotIn("bundle-missing", self.codes())

    def test_missing_node_modules_link_is_caught(self):
        """DSH 从 node_modules 解析 bundle；只写 link: 声明会让插件被静默丢弃。"""
        target = self.tmp / "plug2"
        target.mkdir()
        (target / "index.js").write_text("export default 1\n", encoding="utf-8")
        (target / "package.json").write_text('{"main":"index.js"}', encoding="utf-8")
        pkg = {"dependencies": {"@local/y": f"link:{target.as_posix()}"},
               "dsh": {"profile": {"bundles": ["@local/y"]}}}
        self.write_pkg(pkg)
        P.check_bundles(pkg)
        msgs = [r["msg"] for r in P.results if r["level"] == "FAIL"]
        self.assertTrue(any("静默丢弃" in m for m in msgs), msgs)

    def test_builtin_bundle_not_flagged(self):
        # 不在 dependencies 里的 bundle 由应用内置提供，不该报错
        pkg = {"dependencies": {},
               "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}
        self.write_pkg(pkg)
        P.check_bundles(pkg)
        self.assertNotIn("bundle-missing", self.codes())

    def test_js_tag_yaml_is_legal(self):
        (self.prof / "cordis.patch.yml").write_text(
            "- id: tool-bash\n  disabled: !!js process.platform === 'win32'\n",
            encoding="utf-8")
        P.check_yaml(self.prof / "cordis.patch.yml")
        self.assertNotIn("yaml-parse", self.codes())
        self.assertIn("yaml", [r["code"] for r in P.results if r["level"] == "OK"])

    def test_yaml_shape_caught(self):
        (self.prof / "cordis.patch.yml").write_text("id: not-a-list\n", encoding="utf-8")
        P.check_yaml(self.prof / "cordis.patch.yml")
        self.assertIn("yaml-shape", self.codes())

    def test_yaml_bom_caught(self):
        (self.prof / "cordis.patch.yml").write_bytes(
            "\ufeff- id: a\n".encode("utf-8"))
        P.check_yaml(self.prof / "cordis.patch.yml")
        self.assertIn("bom", self.codes())

    def test_exit_code_matrix(self):
        # 空 bundles 本身就算阻塞（profile 没有内容），这里给一个内置 bundle
        ok_pkg = {"dependencies": {},
                  "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}
        self.write_pkg(ok_pkg)
        (self.prof / "cordis.patch.yml").write_text("- id: a\n", encoding="utf-8")
        rc = P.run(do_drift=False)
        self.assertEqual(rc, 1)          # 只有「没有基线」这类警告
        self.write_pkg(ok_pkg, bom=True)
        P.results.clear()
        rc = P.run(do_drift=False)
        self.assertEqual(rc, 2)

    def test_empty_bundles_is_blocking(self):
        self.write_pkg({"dsh": {"profile": {"bundles": []}}})
        P.check_bundles({"dsh": {"profile": {"bundles": []}}})
        self.assertIn("bundles", self.codes())


    def test_drift_uses_label_repo(self):
        """--label 指向第二个目标的备份仓，不能错用默认仓。"""
        import json as _json
        import lkg as L
        src = {"dependencies": {},
               "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base"]}}}
        self.write_pkg(src)
        home = self.prof.parent.parent
        orig = (L.LKG_DIR, L.BASELINE, P.LABEL)
        L.LKG_DIR = self.tmp / "lkg"
        L.BASELINE = L.LKG_DIR / "baseline.json"
        P.LABEL = "official"
        try:
            cfg = {"home": str(home), "items": [
                {"bucket": "profiles", "path": "profiles/desktop/package.json",
                 "mode": "restore"}]}
            m = L.snapshot("t", cfg=cfg, quiet=True, label="official")
            L.baseline_file("official").write_text(
                _json.dumps({"id": m["id"], "created": m["created"],
                             "file_count": m["file_count"]}), encoding="utf-8")
            P.results.clear()
            P.check_drift()
            codes = [r["code"] for r in P.results]
            self.assertIn("drift", codes)
            self.assertNotIn("baseline-missing", codes)
        finally:
            L.LKG_DIR, L.BASELINE, P.LABEL = orig

    def test_home_flag_switches_profile(self):
        other = self.tmp / "other"
        (other / "profiles" / "desktop").mkdir(parents=True)
        rc = P.main(["--home", str(other), "--no-drift"])
        self.assertIn(rc, (0, 1, 2))
        self.assertEqual(P.PROFILE, other / "profiles" / "desktop")

    def test_help(self):
        self.assertEqual(P.main(["--help"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
