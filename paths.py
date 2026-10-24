#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路径与可执行文件解析 —— 上传前把所有写死的本机路径收拢到这里。

优先级：
  DSH home   : 环境变量 DSH_GUARD_HOME → DSH_HOME → config.json 的 home → 自动探测 → 报错
  project    : 本文件所在目录（项目根）
  pythonw    : config.json 的 pythonw → 当前解释器（若是 pythonw）→ PATH 里的 pythonw → 当前解释器

自动探测只认「有 profiles/desktop/package.json 或 logs/desktop-next*.jsonl」的目录，
常见候选：%USERPROFILE%\\.dsh、.dsh 的 junction 目标、D:\\AI\\DSH-NEXT 之类。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT / "config.json"


def _config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _score(p: Path) -> int:
    """给候选目录打分，取最高分。

    本工具盯的是 DSH **NEXT**，它的特征日志是 `logs/desktop-next*.jsonl`；
    官方版共用 `.dsh`（junction），也放 `profiles/desktop`，所以只看 profiles 会选错。
    """
    score = 0
    try:
        if any(p.glob("logs/desktop-next*.jsonl")) or (p / "logs" / "desktop-next.log").exists():
            score += 2          # NEXT 强特征
        if (p / "profiles" / "desktop" / "package.json").exists():
            score += 1
        if (p / "app").is_dir():
            score += 1
        if (p / "sessions").is_dir():
            score += 1
    except OSError:
        return -1
    return score


def _candidates() -> list[Path]:
    out: list[Path] = []
    home = Path.home()
    out += [home / "AppData" / "Roaming" / ".dsh", home / ".dsh"]
    # 常见安装位置（可用 config.json / DSH_HOME 覆盖）
    out += [Path(r"D:\AI\DSH-NEXT"), Path(r"D:\DSH-NEXT"), Path(r"C:\DSH-NEXT"),
            Path(r"D:\AI\DSH-official\home"), Path(r"C:\DSH")]
    return out


def targets() -> list[dict]:
    """要盯的 DSH 实例列表。

    配置里写 targets 就用它（可以同时盯 NEXT 和官方版）：
        "targets": [{"name": "next", "home": "..."}, {"name": "official", "home": "..."}]
    没写就退化成一个默认目标（home 解析同 dsh_home）。
    """
    cfg = _config()
    raw = cfg.get("targets")
    if isinstance(raw, list) and raw:
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            home = item.get("home")
            if not home:
                continue
            out.append({"name": str(item.get("name") or "default"), "home": Path(home)})
        if out:
            return out
    return [{"name": "default", "home": dsh_home()}]


def dsh_home() -> Path:
    """默认的 DSH 数据目录（logs / profiles / storages / sessions 都在它下面）。"""
    for key in ("DSH_GUARD_HOME", "DSH_HOME"):
        val = os.environ.get(key)
        if val:
            return Path(val)
    cfg = _config().get("home")
    if cfg:
        return Path(cfg)
    scored = [(_score(c), c) for c in _candidates()]
    scored = [(s, c) for s, c in scored if s > 0]
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
    raise SystemExit(
        "✗ 找不到 DSH 数据目录。用下面任一方式指定：\n"
        "  · 设环境变量 DSH_HOME=你的DSH数据目录\n"
        "  · 在 config.json 里写 {\"home\": \"你的DSH数据目录\"}\n"
        "  该目录下应有 logs/desktop-next*.jsonl 或 profiles/desktop/package.json"
    )


def pythonw() -> Path:
    """拉起守护用的 pythonw（无控制台窗口）。"""
    cfg = _config().get("pythonw")
    if cfg and Path(cfg).exists():
        return Path(cfg)
    exe = Path(sys.executable)
    if exe.name.lower().startswith("pythonw"):
        return exe
    found = shutil.which("pythonw.exe") or shutil.which("pythonw")
    if found:
        return Path(found)
    sibling = exe.with_name("pythonw.exe")
    return sibling if sibling.exists() else exe


def python() -> Path:
    """计划任务里用的 python（有控制台，但输出重定向到日志）。"""
    cfg = _config().get("python")
    if cfg and Path(cfg).exists():
        return Path(cfg)
    exe = Path(sys.executable)
    if exe.name.lower().startswith("pythonw"):
        sibling = exe.with_name("python.exe")
        if sibling.exists():
            return sibling
    return exe


def describe() -> dict:
    """给 diag 用：当前解析到的是什么。"""
    info = {"project": str(PROJECT), "config_file": str(CONFIG_FILE), "config_exists": CONFIG_FILE.exists()}
    try:
        info["dsh_home"] = str(dsh_home())
    except SystemExit as e:
        info["dsh_home"] = f"(未解析: {e})"
    info["pythonw"] = str(pythonw())
    info["python"] = str(python())
    return info


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(describe(), ensure_ascii=False, indent=1))
