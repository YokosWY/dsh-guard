#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-guard · 会话记录备份（增量，硬链接去重）

对象：`D:\\AI\\DSH-NEXT\\sessions`（52 MB / 100+ 文件，每轮对话都在追加写）。
策略：每次备份都是完整镜像，但**没变的文件用硬链接指向上一次**——NTFS 上零额外空间，
      变了才真拷。所以 daily 备份看起来是 7 份 52 MB，实际只占变化量。

用法:
  python sessions.py snapshot            # 打一份增量
  python sessions.py list                # 列备份
  python sessions.py restore <id>        # 演算（默认不动手）
  python sessions.py restore <id> --apply  # 真写（要求 DSH 已退出）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import paths  # noqa: E402

import lkg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = paths.dsh_home() / "sessions"
DEST = ROOT / "sessions-backup"
KEEP = 7
SKIP_SUFFIX = {".tmp", ".lock"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def list_backups() -> list[dict]:
    out = []
    if not DEST.exists():
        return out
    for d in sorted(DEST.glob("*/")):
        mf = d / "manifest.json"
        try:
            out.append(json.loads(mf.read_text(encoding="utf-8")))
        except Exception:
            continue
    out.sort(key=lambda m: m.get("created", ""), reverse=True)
    return out


def snapshot(quiet: bool = False) -> dict:
    if not SRC.exists():
        raise SystemExit(f"✗ 源目录不存在：{SRC}")
    sid = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")
    dest = DEST / sid
    if dest.exists():
        sid += "-b"
        dest = DEST / sid
    prev = list_backups()
    prev_dir = DEST / prev[0]["id"] if prev else None

    files, linked, copied, bytes_new = [], 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(SRC):
        for fn in filenames:
            if Path(fn).suffix.lower() in SKIP_SUFFIX:
                continue
            src = Path(dirpath) / fn
            rel = src.relative_to(SRC).as_posix()
            st = src.stat()
            target = dest / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            sig = f"{st.st_size}:{int(st.st_mtime)}"
            prev_entry = None
            if prev_dir:
                pf = prev_dir / "files" / rel
                if pf.exists():
                    pst = pf.stat()
                    if f"{pst.st_size}:{int(pst.st_mtime)}" == sig:
                        prev_entry = pf
            if prev_entry:
                try:
                    os.link(prev_entry, target)
                    linked += 1
                except OSError:
                    shutil.copy2(src, target)
                    copied += 1
                    bytes_new += st.st_size
            else:
                shutil.copy2(src, target)
                copied += 1
                bytes_new += st.st_size
            files.append({"path": rel, "size": st.st_size, "mtime": int(st.st_mtime)})

    manifest = {
        "version": 1,
        "id": sid,
        "created": utcnow().isoformat(timespec="seconds"),
        "source": str(SRC),
        "file_count": len(files),
        "bytes_total": sum(f["size"] for f in files),
        "bytes_new": bytes_new,
        "linked": linked,
        "copied": copied,
        "files": files,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    prune()
    if not quiet:
        print(f"✅ 会话备份 {sid}：{len(files)} 个文件，"
              f"新增 {bytes_new/1048576:.2f} MB（硬链接复用 {linked} 个）")
    return manifest


def prune(keep: int = KEEP) -> None:
    for m in list_backups()[keep:]:
        shutil.rmtree(DEST / m["id"], ignore_errors=True)


def restore(sid: str, apply: bool = False) -> int:
    snap = DEST / sid
    mf = snap / "manifest.json"
    if not mf.exists():
        print(f"✗ 没有备份 {sid}")
        return 2
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    changed, missing = [], []
    for f in manifest["files"]:
        cur = SRC / f["path"]
        if not cur.exists():
            missing.append(f["path"])
        elif cur.stat().st_size != f["size"]:
            changed.append(f["path"])
    print(f"备份 {sid}：{len(manifest['files'])} 个文件；"
          f"大小不同 {len(changed)} 个，缺失 {len(missing)} 个")
    for p in (changed + missing)[:20]:
        print(f"  - {p}")
    if not apply:
        print("\n这是演算。会话是有状态数据，还原会盖掉当前记录；确认后再加 --apply。")
        return 0
    procs = lkg._dsh_processes()
    if procs:
        print(f"✗ DSH 还在跑（{', '.join(procs)}），先完全退出。")
        return 3
    qdir = lkg.QUARANTINE / f"sessions-{utcnow().astimezone().strftime('%Y%m%d-%H%M%S')}"
    done = 0
    for rel in changed + missing:
        src = snap / "files" / rel
        dst = SRC / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            bak = qdir / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bak)
        tmp = dst.with_name(dst.name + f".dshguard-tmp-{os.getpid()}")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        done += 1
    lkg._log_incident("sessions-restore", sid, f"{done} 个文件", changed[:10] + missing[:10])
    print(f"✅ 已还原 {done} 个会话文件；原文件在 {qdir}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="sessions.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_s = sub.add_parser("snapshot")
    p_s.add_argument("--quiet", action="store_true")
    p_r = sub.add_parser("restore")
    p_r.add_argument("id")
    p_r.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        rows = list_backups()
        if not rows:
            print("（还没有会话备份）")
            return 0
        for m in rows:
            print(f"{m['id']}  {m['created']}  {m['file_count']:4d} 文件  "
                  f"总计 {m['bytes_total']/1048576:6.1f} MB  新增 {m['bytes_new']/1048576:5.2f} MB")
        return 0
    if a.cmd == "snapshot":
        snapshot(quiet=a.quiet)
        return 0
    if a.cmd == "restore":
        return restore(a.id, apply=a.apply)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
