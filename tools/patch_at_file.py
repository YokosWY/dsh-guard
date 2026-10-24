#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给 dsh-at-file 打本地补丁：宿主没有 settings.register 时降级为内存设置。

背景：DSH NEXT 2.0.18 的 settings 服务没有 register()（新版由插件 Config schema 派生设置），
dsh-at-file 0.7.0 直接调用它 → apply 抛 TypeError → 整个插件不激活（@ 引用文件功能失效）。

补丁做法：能注册就注册；不能就返回一个基于 schema 默认值的内存 shim（get/update），
插件其余逻辑照常跑。代价：这几项设置在界面上不再持久化。

用法:
  python tools/patch_at_file.py            # 打补丁（先备份）
  python tools/patch_at_file.py --revert   # 回滚到最近的备份
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OLD = '  return ctx.settings.register(AT_FILE_NAMESPACE, AtFileSettingsSchema, { applies: "live" });'
NEW = '''  // [dsh-guard 本地补丁] 宿主没有 settings.register 时降级为内存设置，避免整个插件不激活
  try {
    if (ctx.settings && typeof ctx.settings.register === "function") {
      return ctx.settings.register(AT_FILE_NAMESPACE, AtFileSettingsSchema, { applies: "live" });
    }
    ctx.logger?.("dsh-at-file").warn?.(
      "host 的 settings 服务没有 register()，改用内存默认值（设置界面不再持久化）");
  } catch (error) {
    ctx.logger?.("dsh-at-file").warn?.("注册设置失败，改用内存默认值：" + String(error));
  }
  const fallback = AtFileSettingsSchema({});
  return {
    get() {
      return fallback;
    },
    async update(patch) {
      Object.assign(fallback, patch);
      return fallback;
    }
  };'''


def plugin_file() -> Path:
    home = paths.dsh_home()
    return home / "profiles" / "desktop" / "node_modules" / "dsh-at-file" / "lib" / "index.js"


def main(argv: list[str]) -> int:
    f = plugin_file()
    if not f.exists():
        print(f"✗ 找不到 {f}")
        return 2

    src = f.read_text(encoding="utf-8")

    if "--revert" in argv:
        backups = sorted(f.parent.glob("index.js.bak-atfile-*"))
        if not backups:
            print("✗ 没有备份可回滚")
            return 2
        shutil.copy2(backups[-1], f)
        print(f"✅ 已回滚到 {backups[-1].name}")
        return 0

    if "dsh-guard 本地补丁" in src:
        print("已经打过补丁，跳过")
        return 0
    if OLD not in src:
        print("✗ 源码形状和预期不一致（插件可能升级了），中止")
        print(f"  找的是: {OLD.strip()[:70]}")
        return 3

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f.with_name(f"index.js.bak-atfile-{stamp}")
    shutil.copy2(f, backup)
    f.write_text(src.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"✅ 已打补丁（备份 {backup.name}）")
    print("   重启 DSH 后，日志里应不再出现 dsh-at-file 的 did not activate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
