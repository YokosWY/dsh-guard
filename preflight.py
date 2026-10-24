#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dsh-guard · 启动前只读体检（P1）

只读：不修、不写、不删。发现问题只报，改不改由你定。
查的都是这 8 天真实踩过的坑：
  · JSON/YAML 带 BOM（10-17 那次启动失败就是它）
  · bundles 里的 link: 目标目录不存在 / 入口文件缺失
  · cordis.patch.yml 语法坏或顶层不是数组
  · 上一轮残留进程还占着端口
  · 当前配置与基线漂移（提醒，不拦）

用法:
  python preflight.py            # 体检
  python preflight.py --json     # 机器可读
  python preflight.py --no-drift # 跳过基线漂移检查
退出码: 0 通过；1 有警告；2 有阻塞问题
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

# 输出被重定向时 Windows 会用 cp936，遇到 ✅ 这类字符直接 UnicodeEncodeError；统一成 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import lkg  # noqa: E402

HOME = lkg.HOME
PROFILE = HOME / "profiles" / "desktop"
BOM = b"\xef\xbb\xbf"
PORT = 57120

results: list[dict] = []


def add(level: str, code: str, msg: str, fix: str = "") -> None:
    results.append({"level": level, "code": code, "msg": msg, "fix": fix})


def has_bom(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            return f.read(3) == BOM
    except OSError:
        return False


def check_json(p: Path, required_key: str | None = None) -> dict | None:
    if not p.exists():
        add("FAIL", "missing", f"缺少文件 {p.relative_to(HOME).as_posix()}",
            "从基线还原：python lkg.py restore <id> --apply")
        return None
    if has_bom(p):
        add("FAIL", "bom", f"{p.name} 带 UTF-8 BOM，DSH 解析 JSON 会直接失败",
            '重写为无 BOM：pwsh -c "[IO.File]::WriteAllText(\'<path>\', <text>, '
            '(New-Object Text.UTF8Encoding($false)))"')
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:
        add("FAIL", "json-parse", f"{p.name} 解析失败：{e}",
            "从基线还原或手工修语法后重试")
        return None
    if required_key and required_key not in data:
        add("FAIL", "json-shape", f"{p.name} 缺少 {required_key}",
            "这个字段是 profile 必需的，别手删")
    else:
        add("OK", "json", f"{p.name} 解析正常")
    return data


def _yaml_loader():
    """cordis.patch.yml 合法使用 `!!js <表达式>`，标准 SafeLoader 会当成未知标签报错。"""
    import yaml  # type: ignore

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("tag:yaml.org,2002:js", lambda l, s, n: "<js>")
    return yaml, Loader


def check_yaml(p: Path) -> None:
    if not p.exists():
        add("FAIL", "missing", f"缺少 {p.name}", "从基线还原")
        return
    if has_bom(p):
        add("FAIL", "bom", f"{p.name} 带 UTF-8 BOM，YAML 前会有隐形字符",
            "重写成无 BOM")
    try:
        yaml, Loader = _yaml_loader()
    except Exception:
        add("WARN", "yaml-lib", "没装 PyYAML，跳过 YAML 语法检查", "pip install pyyaml")
        return
    try:
        data = yaml.load(p.read_text(encoding="utf-8-sig"), Loader=Loader)
    except Exception as e:
        add("FAIL", "yaml-parse", f"{p.name} 语法错误：{str(e).splitlines()[0]}",
            "从基线还原；这类错会让 DSH 起不来")
        return
    if not isinstance(data, list):
        add("FAIL", "yaml-shape", f"{p.name} 顶层不是数组（现在是 {type(data).__name__}）",
            "patch 层必须是一个 YAML 数组")
    else:
        bad = [i for i, x in enumerate(data) if not isinstance(x, dict)]
        if bad:
            add("WARN", "yaml-items", f"{p.name} 有 {len(bad)} 个非字典条目（行号索引 {bad[:5]}）",
                "检查这些条目是否写成了纯标量")
        else:
            add("OK", "yaml", f"{p.name} 解析正常（{len(data)} 条）")


def check_bundles(pkg: dict | None) -> None:
    if not pkg:
        return
    bundles = ((pkg.get("dsh") or {}).get("profile") or {}).get("bundles")
    if not isinstance(bundles, list) or not bundles:
        add("FAIL", "bundles", "package.json 里 dsh.profile.bundles 为空或不是数组",
            "从基线还原 package.json")
        return
    deps = pkg.get("dependencies") or {}
    nm = PROFILE / "node_modules"
    missing, builtin = [], 0
    for b in bundles:
        b = str(b)
        if b not in deps:
            # 不在本机 dependencies 里 → 由应用内置提供（app.asar），不由本机安装，跳过
            builtin += 1
            continue
        spec = str(deps.get(b, ""))
        if spec.startswith("link:") or b.startswith("@local/"):
            if not spec.startswith("link:"):
                missing.append(f"{b}（@local/ 但依赖里没写 link:）")
                continue
            target = Path(spec[5:])
            if not target.exists():
                missing.append(f"{b} → link 目标不存在：{target}")
                continue
            pj = target / "package.json"
            if not pj.exists():
                missing.append(f"{b} → 目标里没有 package.json")
                continue
            try:
                meta = json.loads(pj.read_text(encoding="utf-8-sig"))
            except Exception as e:
                missing.append(f"{b} → package.json 坏了：{e}")
                continue
            entry = meta.get("main") or ""
            if entry and not (target / entry).exists():
                missing.append(f"{b} → 入口缺文件：{entry}")
            # DSH 是从 profile 的 node_modules 解析 bundle 的，只有 link: 声明不够。
            # 少了这个软链，bundle 会被静默丢掉（cordis.yml 里都不会出现），不报错也不崩。
            if not (nm / b).exists():
                missing.append(
                    f"{b} → node_modules 里没有软链，bundle 会被静默丢弃"
                    f"（修：mklink /J \"{nm / b}\" \"{target}\"）")
        else:
            if not (nm / b).exists():
                missing.append(f"{b}（node_modules 里没有）")
    if missing:
        add("FAIL", "bundle-missing", f"{len(missing)} 个本机 bundle 解析不到",
            "逐个看下面的条目")
        for m in missing:
            add("FAIL", "bundle", m)
    else:
        local = [b for b in map(str, bundles) if b in deps]
        add("OK", "bundles",
            f"{len(bundles)} 个 bundle：本机安装 {len(local)} 个全部可解析，"
            f"内置 {builtin} 个跳过")


def check_port() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    busy = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()
    if busy:
        add("WARN", "port", f"127.0.0.1:{PORT} 已被占用（DSH 可能正在运行）",
            "确认不是残留进程；还原配置前必须完全退出 DSH")
    else:
        add("OK", "port", f"127.0.0.1:{PORT} 空闲")


def check_processes() -> None:
    procs = lkg._dsh_processes()
    if procs:
        add("WARN", "process", f"DSH 进程还在：{', '.join(procs)}",
            "还原配置前必须完全退出（含托盘/后台）")
    else:
        add("OK", "process", "没有 DSH 进程在跑")


def check_storages() -> None:
    sd = HOME / "storages"
    if not sd.exists():
        add("WARN", "storages", "没有 storages 目录", "")
        return
    bad = []
    for p in sorted(sd.glob("*.json")):
        if has_bom(p):
            bad.append(f"{p.name}（BOM）")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            bad.append(p.name)
    if bad:
        add("WARN", "storages-json", f"storages 里有解析不了的 JSON：{', '.join(bad)}",
            "插件可能读默认值；不影响启动")
    else:
        add("OK", "storages", "storages 下的 JSON 都可解析")


def check_drift() -> None:
    base = lkg.load_baseline()
    if not base:
        add("WARN", "no-baseline", "还没有基线", "python lkg.py snapshot && python lkg.py promote <id> --yes")
        return
    snap = lkg.LKG_DIR / base["id"] / "manifest.json"
    if not snap.exists():
        add("WARN", "baseline-missing", f"基线 {base['id']} 的快照文件不见了", "重新 snapshot + promote")
        return
    manifest = json.loads(snap.read_text(encoding="utf-8"))
    rows = lkg.diff_against_snapshot(manifest)
    drift = [r for r in rows if r["state"] != "same" and r["mode"] == "restore"]
    if drift:
        add("WARN", "drift", f"相对基线（{base['id']}）有 {len(drift)} 个文件变了",
            "如果这些改动是你自己做的、且能正常启动，就打个新基线：python lkg.py snapshot"
            " && python lkg.py promote <新id> --yes")
        for r in drift[:8]:
            add("WARN", "drift-item", f"[{r['state']}] {r['path']}")
    else:
        add("OK", "drift", f"与基线 {base['id']} 一致")


def run(do_drift: bool = True) -> int:
    check_json(PROFILE / "package.json", required_key="dsh")
    pkg_path = PROFILE / "package.json"
    pkg = None
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8-sig"))
        except Exception:
            pkg = None
    check_bundles(pkg)
    check_yaml(PROFILE / "cordis.patch.yml")
    for name in ("desktop-next.runtime.patch.json", "desktop-next.cordis.patch.json"):
        p = PROFILE / name
        if p.exists():
            check_json(p)
    check_storages()
    check_processes()
    check_port()
    if do_drift:
        check_drift()

    fails = [r for r in results if r["level"] == "FAIL"]
    warns = [r for r in results if r["level"] == "WARN"]
    return 2 if fails else (1 if warns else 0)


def render(rc: int) -> str:
    icon = {"OK": "✓", "WARN": "!", "FAIL": "✗"}
    lines = []
    for r in results:
        lines.append(f"  {icon.get(r['level'], '?')} [{r['code']}] {r['msg']}")
    fails = [r for r in results if r["level"] == "FAIL"]
    warns = [r for r in results if r["level"] == "WARN"]
    head = {0: "✅ 体检通过", 1: f"⚠ 体检有 {len(warns)} 个警告", 2: f"✗ 体检发现 {len(fails)} 个阻塞问题"}[rc]
    out = [head, *lines]
    if fails:
        out.append("\n修法：")
        for r in fails:
            if r["fix"]:
                out.append(f"  · {r['msg']} → {r['fix']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    rc = run(do_drift="--no-drift" not in argv)
    if "--json" in argv:
        print(json.dumps({"rc": rc, "results": results}, ensure_ascii=False, indent=1))
        return rc
    print(render(rc))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
