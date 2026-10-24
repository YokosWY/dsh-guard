#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""dsh-guard · LKG 备份仓（P1）

为什么不用 DSH 自带的 `recovery\`：
  `recovery\<ts>\snapshot.json` 会在 Host ready 后约 0.3 秒写 `healthy: true`，
  而实测有「写 healthy 快照后 0.55 秒就 runtime.failed」的案例 —— 它会把即将崩的配置标成好份。
  所以这里自建一份，谁也不覆盖谁。

写配置的三条保险（restore --apply 时强制走完）：
  1. 三查：无 DSH 进程、目标文件 mtime 10 秒未变、拿到 lkg 的锁
  2. 原子写：同目录 temp → fsync → os.replace，原文件先拷进 quarantine/<ts>/
  3. 写后校验：重算 sha256，不符就整批回滚并退出非 0

用法:
  python lkg.py list                        # 列快照
  python lkg.py snapshot --reason manual    # 打一份
  python lkg.py verify <id>                 # 和磁盘比，看漂移
  python lkg.py restore <id>                # 只演算（dry-run）
  python lkg.py restore <id> --apply        # 真写（需无 DSH 进程）
  python lkg.py promote <id> --yes          # 立为基线（要求满足提升条件）
  python lkg.py baseline                    # 看当前基线
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# 输出被重定向时 Windows 会用 cp936，遇到 ✅ 这类字符直接 UnicodeEncodeError；统一成 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
import paths

HOME = paths.dsh_home()
LKG_DIR = ROOT / "lkg"
QUARANTINE = ROOT / "quarantine"
LOGS = ROOT / "logs"
BASELINE = LKG_DIR / "baseline.json"
LOCK = LKG_DIR / ".lock"
CONFIG = ROOT / "config.json"

KEEP = 14
MTIME_QUIET_SECONDS = 10
SKIP_DIRS = {"node_modules", ".git", "_backup", "_diag", "__pycache__"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ts_id(t: datetime | None = None) -> str:
    return (t or utcnow()).astimezone().strftime("%Y%m%d-%H%M%S")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 配置

DEFAULT_CONFIG = {
    "home": str(HOME),
    "profile": "desktop",
    "keep": KEEP,
    # mode: restore=会写回；reference=只留档不写回
    "items": [
        {"bucket": "profiles", "path": "profiles/desktop/package.json", "mode": "restore"},
        {"bucket": "profiles", "path": "profiles/desktop/cordis.patch.yml", "mode": "restore"},
        {"bucket": "profiles", "path": "profiles/desktop/desktop-next.runtime.patch.json", "mode": "restore"},
        {"bucket": "profiles", "path": "profiles/desktop/desktop-next.cordis.patch.json", "mode": "restore"},
        {"bucket": "profiles", "path": "profiles/desktop/compatibility.json", "mode": "reference"},
        # cordis.yml 是应用启动时自动重写的，只留档
        {"bucket": "profiles", "path": "profiles/desktop/cordis.yml", "mode": "reference"},
        {"bucket": "local-plugins", "path": "local-plugins", "mode": "restore", "tree": True},
        {"bucket": "storages", "path": "storages/clarify-tier.json", "mode": "restore"},
        {"bucket": "storages", "path": "storages/loop-guard", "mode": "restore", "tree": True},
        {"bucket": "storages", "path": "storages/mcp_connector.json", "mode": "reference"},
        {"bucket": "storages", "path": "storages/schedule.json", "mode": "reference"},
    ],
}


def load_config() -> dict:
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
            # 下划线开头的键当注释；items 为空表示「用内置列表」，别把默认项清掉
            cfg = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
            if not cfg.get("items"):
                cfg.pop("items", None)
            base = json.loads(json.dumps(DEFAULT_CONFIG))
            base.update(cfg)
            return base
        except Exception as e:
            print(f"⚠ config.json 解析失败，用默认配置：{e}")
    return json.loads(json.dumps(DEFAULT_CONFIG))


@dataclass
class Item:
    bucket: str
    rel: str
    mode: str = "restore"
    tree: bool = False
    src: Path = field(default=None)

    def matches(self, p: Path) -> bool:
        return p.is_file() and p.exists()


def expand_items(cfg: dict) -> list[Item]:
    home = Path(cfg["home"])
    by_path: dict[str, Item] = {}
    for spec in cfg["items"]:
        item = Item(bucket=spec["bucket"], rel=spec["path"],
                    mode=spec.get("mode", "restore"), tree=bool(spec.get("tree")),
                    src=home / spec["path"])
        prev = by_path.get(item.rel)
        # 同一路径写两次时，restore 优先（reference 只是留档）
        if prev is None or (prev.mode == "reference" and item.mode == "restore"):
            by_path[item.rel] = item
    return list(by_path.values())


def iter_files(item: Item) -> list[Path]:
    p = item.src
    if not p.exists():
        return []
    if p.is_file():
        return [p]
    out = []
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            fp = Path(dirpath) / fn
            if fp.is_file():
                out.append(fp)
    return sorted(out)


# ---------------------------------------------------------------- 快照

def _content_signature(files: list[dict]) -> str:
    """内容指纹：只看 path+sha256，和 reason / 时间无关。

    只算配置面（profiles + local-plugins）。storages 里的插件状态是运行期活写
    （例如 clarify-tier.json 每个会话都会变），拿它参与指纹会导致天天"内容不同"，
    去重直接失效。
    """
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["path"]):
        if f.get("bucket") == "storages":
            continue
        h.update(f["path"].encode("utf-8"))
        h.update(f["sha256"].encode("ascii"))
    return h.hexdigest()


def snapshot(reason: str = "manual", cfg: dict | None = None, quiet: bool = False,
             skip_identical: bool = False) -> dict:
    cfg = cfg or load_config()
    home = Path(cfg["home"])
    sid = ts_id()
    dest = LKG_DIR / sid
    if dest.exists():
        sid += "-b"
        dest = LKG_DIR / sid
    files = []
    for item in expand_items(cfg):
        for fp in iter_files(item):
            rel = fp.relative_to(home).as_posix()
            target = dest / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, target)
            files.append({
                "path": rel,
                "bucket": item.bucket,
                "mode": item.mode,
                "sha256": sha256_file(fp),
                "size": fp.stat().st_size,
            })

    if skip_identical:
        # 每日定时任务用这个：内容没变就不落新快照，否则 14 份全被同样的内容顶掉，
        # 真出事时反而没有「上周那一版」可回。
        snaps = list_snapshots()
        if snaps and _content_signature(snaps[0]["files"]) == _content_signature(files):
            if not quiet:
                print(f"内容与最新快照 {snaps[0]['id']} 一致，跳过（不占位）")
            return snaps[0]

    manifest = {
        "version": 1,
        "id": sid,
        "created": utcnow().isoformat(timespec="seconds"),
        "reason": reason,
        "home": str(home),
        "profile": cfg.get("profile", "desktop"),
        "file_count": len(files),
        "bytes": sum(f["size"] for f in files),
        "files": files,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    prune(int(cfg.get("keep", KEEP)), quiet=quiet)
    if not quiet:
        print(f"✅ 快照 {sid}  reason={reason}  {len(files)} 个文件  "
              f"{manifest['bytes']/1048576:.2f} MB")
    return manifest


def list_snapshots() -> list[dict]:
    out = []
    for d in sorted(LKG_DIR.glob("*/")):
        mf = d / "manifest.json"
        if mf.exists():
            try:
                out.append(json.loads(mf.read_text(encoding="utf-8")))
            except Exception:
                continue
    out.sort(key=lambda m: m.get("created", ""), reverse=True)
    return out


def prune(keep: int, quiet: bool = True) -> list[str]:
    snaps = list_snapshots()
    base_id = (load_baseline() or {}).get("id")
    removed = []
    for m in snaps[keep:]:
        if m["id"] == base_id:
            continue
        d = LKG_DIR / m["id"]
        shutil.rmtree(d, ignore_errors=True)
        removed.append(m["id"])
    if removed and not quiet:
        print(f"  清理旧快照 {len(removed)} 份：{', '.join(removed)}")
    return removed


# ---------------------------------------------------------------- 基线

def load_baseline() -> dict | None:
    if BASELINE.exists():
        try:
            return json.loads(BASELINE.read_text(encoding="utf-8-sig"))
        except Exception:
            return None
    return None


def promote(sid: str, yes: bool = False, note: str = "", force: bool = False) -> dict:
    """立为基线。

    提升条件（除非 --force）：
      1. 快照与磁盘一致（没漂移）——漂移过的快照代表「已经跑在别的东西上」，不能当基线
      2. 最近一次崩溃之后，至少又成功跑过 3 次；或当前这次已经稳定运行 ≥ 6 小时
    不满足就拒绝，并说明缺哪条。
    """
    snap = LKG_DIR / sid
    if not (snap / "manifest.json").exists():
        raise SystemExit(f"✗ 没有快照 {sid}")
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))

    if not force:
        drift = [r for r in diff_against_snapshot(manifest) if r["state"] != "same"]
        if drift:
            raise SystemExit(
                f"✗ 快照与磁盘不一致（{len(drift)} 个文件变了），先重新 snapshot：\n"
                + "\n".join(f"   [{r['state']}] {r['path']}" for r in drift[:6]))
        ok, why = _health_ok_for_promote()
        if not ok:
            raise SystemExit(f"✗ 提升条件不满足：{why}\n  （确认无误可加 --force）")

    if not yes:
        print(f"将把 {sid} 立为基线（{manifest['file_count']} 个文件，{manifest['created']}）")
        print("加 --yes 执行。")
        return manifest
    baseline = {
        "id": sid,
        "created": manifest["created"],
        "promoted_at": utcnow().isoformat(timespec="seconds"),
        "note": note,
        "file_count": manifest["file_count"],
    }
    BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 基线 = {sid}（{manifest['created']}）{note}")
    return baseline


def _health_ok_for_promote(min_ok_runs: int = 3, quiet_hours: float = 6.0) -> tuple[bool, str]:
    """用 detect.py 的历史判定：最近一次崩溃之后成功跑过几次，或当前已稳定多久。"""
    try:
        import detect  # 同目录，按需导入避免循环
    except Exception as e:
        return False, f"判定器不可用（{e}），无法确认健康"
    runs = sorted(detect.analyse().values(), key=lambda r: r.start)
    bad = {"crash", "boot-fail", "unclean-unknown"}
    last_bad = None
    for r in runs:
        if r.kind in bad:
            last_bad = r
    ok_after = sum(1 for r in runs
                   if r.kind == "ok" and (last_bad is None or r.start > last_bad.start))
    if ok_after >= min_ok_runs:
        return True, ""
    cur = runs[-1] if runs else None
    if cur and cur.kind == "in-progress" and cur.duration >= quiet_hours * 3600:
        return True, ""
    return False, (f"最近一次崩溃在有 {ok_after} 次成功启动之后，"
                   f"当前累计运行 {cur.duration/3600:.1f}h（需要 ≥{min_ok_runs} 次 或 ≥{quiet_hours}h）")


# ---------------------------------------------------------------- 校验与还原

def diff_against_snapshot(manifest: dict, cfg: dict | None = None) -> list[dict]:
    # 以快照记录里的 home 为准：快照可能来自别处，不能拿当前配置去比
    home = Path(manifest.get("home") or (cfg or load_config())["home"])
    rows = []
    for f in manifest["files"]:
        cur = home / f["path"]
        if not cur.exists():
            rows.append({**f, "state": "missing"})
        else:
            now = sha256_file(cur)
            rows.append({**f, "state": "same" if now == f["sha256"] else "drift",
                         "current": now})
    return rows


def verify_snapshot(sid: str) -> int:
    snap = LKG_DIR / sid
    mf = snap / "manifest.json"
    if not mf.exists():
        print(f"✗ 没有快照 {sid}")
        return 2
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    rows = diff_against_snapshot(manifest)
    bad = [r for r in rows if r["state"] != "same"]
    print(f"快照 {sid}：{len(rows)} 个文件，漂移 {len(bad)}")
    for r in bad:
        print(f"  [{r['state']:7s}] {r['mode']:9s} {r['path']}")
    return 0 if not bad else 1


def _dsh_processes() -> list[str]:
    try:
        import psutil  # type: ignore
    except Exception:
        return []
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            name = (p.info.get("name") or "")
            if "dsh next" in name.lower() or name.lower() in ("dsh next.exe",):
                out.append(f"{p.info['pid']}:{name}")
        except Exception:
            continue
    return out


def _acquire_lock() -> Path:
    LKG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        age = time.time() - LOCK.stat().st_mtime
        if age < 300:
            raise SystemExit(f"✗ 已有锁 {LOCK}（{int(age)} 秒前），可能另一个 restore 在跑")
        LOCK.unlink(missing_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} {utcnow().isoformat()}\n")
    return LOCK


def restore(sid: str, apply: bool = False, include_reference: bool = False) -> int:
    snap = LKG_DIR / sid
    mf = snap / "manifest.json"
    if not mf.exists():
        print(f"✗ 没有快照 {sid}")
        return 2
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    home = Path(manifest["home"])
    targets = [f for f in manifest["files"]
               if f["mode"] == "restore" or include_reference]
    rows = diff_against_snapshot({**manifest, "files": targets})
    todo = [r for r in rows if r["state"] != "same"]

    print(f"快照 {sid}（{manifest['created']}）：{len(targets)} 个可还原文件，"
          f"需要动 {len(todo)} 个")
    for r in todo:
        print(f"  [{r['state']:7s}] {r['path']}")
    if not todo:
        print("无需改动。")
        return 0
    if not apply:
        print("\n这是演算。确认无误后加 --apply（要求 DSH 已完全退出）。")
        return 0

    # ---- 三查 ----
    procs = _dsh_processes()
    if procs:
        print(f"✗ DSH 还在跑：{', '.join(procs)}\n  先完全退出再还原。")
        return 3
    lock = _acquire_lock()
    try:
        qdir = QUARANTINE / ts_id()
        qdir.mkdir(parents=True, exist_ok=True)
        written: list[tuple[Path, Path | None]] = []
        try:
            for r in todo:
                dst = home / r["path"]
                src = snap / "files" / r["path"]
                if not src.exists():
                    raise RuntimeError(f"快照里缺文件 {r['path']}")
                # mtime 静默检查
                if dst.exists():
                    age = time.time() - dst.stat().st_mtime
                    if age < MTIME_QUIET_SECONDS:
                        raise RuntimeError(
                            f"{r['path']} 刚被改过（{int(age)} 秒前），等安静 {MTIME_QUIET_SECONDS} 秒再试")
                dst.parent.mkdir(parents=True, exist_ok=True)
                bak = None
                if dst.exists():
                    bak = qdir / r["path"]
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, bak)
                tmp = dst.with_name(dst.name + f".dshguard-tmp-{os.getpid()}")
                shutil.copy2(src, tmp)
                with tmp.open("rb+") as f:
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, dst)
                written.append((dst, bak))
                # 写后校验
                if sha256_file(dst) != r["sha256"]:
                    raise RuntimeError(f"写后校验失败：{r['path']}")
                print(f"  ✓ {r['path']}")
        except Exception as e:
            print(f"✗ 还原中断：{e}\n  回滚已写的 {len(written)} 个文件…")
            for dst, bak in reversed(written):
                try:
                    if bak and bak.exists():
                        tmp = dst.with_name(dst.name + f".dshguard-rb-{os.getpid()}")
                        shutil.copy2(bak, tmp)
                        os.replace(tmp, dst)
                        print(f"  ↺ 回滚 {dst.name}")
                    elif not bak:
                        dst.unlink(missing_ok=True)
                except Exception as e2:
                    print(f"  ✗ 回滚失败 {dst}：{e2}")
            _log_incident("restore-failed", sid, str(e), [r["path"] for r in todo])
            return 4
        _log_incident("restore-ok", sid, f"{len(written)} 个文件", [r["path"] for r in todo])
        print(f"\n✅ 已还原 {len(written)} 个文件；原文件在 {qdir}")
        print("   下次启动前可先跑 python preflight.py 体检。")
        return 0
    finally:
        lock.unlink(missing_ok=True)


def _log_incident(kind: str, sid: str, detail: str, files: list[str]) -> None:
    LOGS.mkdir(exist_ok=True)
    rec = {
        "t": utcnow().isoformat(timespec="seconds"),
        "kind": kind,
        "snapshot": sid,
        "detail": detail,
        "files": files,
        "pid": os.getpid(),
    }
    with (LOGS / "incidents.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="lkg.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    sub.add_parser("baseline")
    p_snap = sub.add_parser("snapshot")
    p_snap.add_argument("--reason", default="manual")
    p_snap.add_argument("--quiet", action="store_true")
    p_snap.add_argument("--skip-identical", action="store_true",
                        help="内容与最新快照一致就不落新份（每日定时任务用）")

    p_ver = sub.add_parser("verify")
    p_ver.add_argument("id")

    p_res = sub.add_parser("restore")
    p_res.add_argument("id")
    p_res.add_argument("--apply", action="store_true")
    p_res.add_argument("--include-reference", action="store_true")

    p_pro = sub.add_parser("promote")
    p_pro.add_argument("id")
    p_pro.add_argument("--yes", action="store_true")
    p_pro.add_argument("--force", action="store_true")
    p_pro.add_argument("--note", default="")

    a = ap.parse_args(argv)
    if a.cmd == "list":
        snaps = list_snapshots()
        if not snaps:
            print("（还没有快照）先跑 python lkg.py snapshot")
            return 0
        base = (load_baseline() or {}).get("id")
        for m in snaps:
            flag = "★" if m["id"] == base else " "
            print(f"{m['id']} {flag} {m['created']}  {m['file_count']:3d} 文件  "
                  f"{m['bytes']/1048576:5.2f} MB  {m['reason']}")
        return 0
    if a.cmd == "baseline":
        b = load_baseline()
        print(json.dumps(b, ensure_ascii=False, indent=1) if b else "（还没有基线）")
        return 0
    if a.cmd == "snapshot":
        snapshot(a.reason, quiet=a.quiet, skip_identical=a.skip_identical)
        return 0
    if a.cmd == "verify":
        return verify_snapshot(a.id)
    if a.cmd == "restore":
        return restore(a.id, apply=a.apply, include_reference=a.include_reference)
    if a.cmd == "promote":
        promote(a.id, yes=a.yes, note=a.note, force=a.force)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
