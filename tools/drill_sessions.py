#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""会话备份还原演练：拿**真实会话目录的副本**跑完整还原流程，不碰原件。

为什么不在真目录上演练：会话 jsonl 是有状态数据，演练删文件/改内容风险太大。
这里整目录复制到临时目录（约 52 MB），把 sessions.SRC / DEST / quarantine 全指过去，
跑的就是生产代码路径（snapshot → 改坏 → dry-run → apply → 校验 → quarantine）。

用法:
  python tools/drill_sessions.py            # 演练 + 打印结果
  python tools/drill_sessions.py --keep     # 保留临时目录（想自己翻现场时用）
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

import lkg  # noqa: E402
import sessions  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REAL = paths.dsh_home() / "sessions"
CORRUPT = b'{"corrupted":true}\n'


def main(argv: list[str]) -> int:
    if not REAL.exists():
        print(f"✗ 没有真实会话目录：{REAL}")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="dshguard-drill-"))
    print(f"沙箱：{tmp}")

    src = tmp / "sessions"
    print("▶ 复制真实会话目录…")
    shutil.copytree(REAL, src)
    n_files = sum(1 for _ in src.rglob("*") if _.is_file())
    print(f"  {n_files} 个文件")

    orig = (sessions.SRC, sessions.DEST, lkg.QUARANTINE, lkg.LOGS,
            lkg._dsh_processes, lkg._log_incident)
    sessions.SRC = src
    sessions.DEST = tmp / "backup"
    lkg.QUARANTINE = tmp / "quarantine"
    lkg.LOGS = tmp / "logs"
    lkg._dsh_processes = lambda: []
    lkg._log_incident = lambda *a, **k: None

    try:
        print("\n▶ 1/5 打备份")
        m = sessions.snapshot()
        print(f"  快照 {m['id']}，{m['file_count']} 个文件")

        targets = sorted(p for p in src.rglob("*") if p.is_file())
        modify, delete = targets[0], targets[-1]
        print(f"\n▶ 2/5 造故障：改 {modify.relative_to(src).as_posix()}，删 "
              f"{delete.relative_to(src).as_posix()}")
        modify.write_bytes(CORRUPT)   # 用 bytes 写：text 模式会把 \n 翻成 \r\n，大小就对不上了
        delete.unlink()

        print("\n▶ 3/5 演算（不应动文件）")
        rc = sessions.restore(m["id"], apply=False)
        assert rc == 0, f"dry-run 返回 {rc}"
        assert modify.stat().st_size == len(CORRUPT), "dry-run 竟然改了文件"
        assert not delete.exists(), "dry-run 竟然恢复了文件"
        print("  ✓ 演算没动文件")

        print("\n▶ 4/5 真还原")
        rc = sessions.restore(m["id"], apply=True)
        assert rc == 0, f"apply 返回 {rc}"

        print("\n▶ 5/5 校验")
        sizes = {f["path"]: f["size"] for f in m["files"]}
        rel_mod = modify.relative_to(src).as_posix()
        rel_del = delete.relative_to(src).as_posix()
        assert delete.exists(), "删掉的文件没回来"
        assert delete.stat().st_size == sizes[rel_del], "补回的文件大小不对"
        assert modify.stat().st_size == sizes[rel_mod], "改坏的文件没还原"
        q = [p for p in lkg.QUARANTINE.rglob("*") if p.is_file()]
        assert any(p.name == modify.name for p in q), "quarantine 里没有原文件"
        assert any(p.stat().st_size == len(CORRUPT) for p in q), "quarantine 里留的不是被改坏的那份"
        print("  ✓ 内容还原、缺失补回、坏文件进 quarantine")

        print("\n✅ 全流程通过（快照 → 改坏 → 演算 → 还原 → 校验）")
        return 0
    finally:
        (sessions.SRC, sessions.DEST, lkg.QUARANTINE, lkg.LOGS,
         lkg._dsh_processes, lkg._log_incident) = orig
        if "--keep" in argv:
            print(f"\n临时目录保留：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
