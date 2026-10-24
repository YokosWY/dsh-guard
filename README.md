# dsh-guard

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](docs/INSTALL.md)
[![platform](https://img.shields.io/badge/platform-windows-lightgrey.svg)](docs/INSTALL.md)

dsh-guard 是给 DSH NEXT（DeepSeek Harness 桌面版）做崩溃判定与恢复的单机工具：一个 Python 命令行 + 一个界面插件，读取 DSH 的日志工作，不修改 DSH 本体。

## 特性

- **判定：** 按 `runId` 聚合 UTC 分卷日志，叠加 Windows 关机事件（1074 → 6006），输出 7 类结局；对 78 次历史启动回放，漏报 0、误报 0
- **体检：** `preflight.py` 只读检查 BOM、JSON/YAML 语法、`link:` 目标、`node_modules` 软链、端口、基线漂移，退出码 0/1/2
- **还原：** 写前三查（无 DSH 进程、目标 mtime 10 秒未变、持锁），同目录 temp → fsync → `os.replace`，写后重算 sha256，不符即整批回滚
- **备份：** 配置快照 57 文件 / 0.83 MB 一份；会话记录走硬链接增量，第二份新增 0.00 MB
- **守护：** 120 秒一轮，只记录与通知；`unclean-unknown` 会留现场文件（事件尾巴 + 文本日志 + 进程快照）
- **开关：** 会话头部按钮写 `state/control.json` 的 `enabled`，只启停扫描与通知

## 前提

- Windows 10/11
- Python 3.11 及以上（仅标准库；`psutil`、`PyYAML` 可选）
- DSH NEXT 安装在同一台机器上（工具读它的 `logs/`、`profiles/`、`sessions/`）

## 安装

```bat
git clone https://github.com/YokosWY/dsh-guard.git <project-dir>
cd /d <project-dir>
copy config.example.json config.json
python paths.py
```

`python paths.py` 打印解析到的 `dsh_home` / `python` / `pythonw` 三项；任一项不对就改 `config.json`，或设 `DSH_HOME`。

## 快速开始

```bat
python detect.py
```

输出四行以内：

```
run 总数 84  需要处理 31  可由配置恢复 3
结局分布: {'ok': 27, 'boot-fail': 19, ...}
崩因分布: {'shell-load': 18, 'silent': 9, ...}
```

完整逐条用 `python detect.py --list`。这一步只读，不改任何东西。

## 边界

- 不修 DSH 本体：壳页加载失败（`ERR_FAILED ... dsh-app://app/`）属应用内部，这里只出证据
- `unclean-unknown` 不猜死因，只留现场
- 不接管 DSH 自带的 `recovery/`：那份快照会把即将崩的配置标成 healthy，所以备份仓自建
- 计划任务仅在当前用户登录后生效；注销或关机期间不运行

## 文档

- [安装与卸载](docs/INSTALL.md)：建基线、装插件（三件事缺一不可）、注册任务
- [结构](docs/ARCHITECTURE.md)：数据流与五条关键口径
- [开发约定](docs/DEVELOP.md)：改代码前必读的坑与检查清单

## 贡献

改完先跑 `自检.cmd`（5 个测试文件 + 78 次回放 + 当前配置体检），再读 [docs/DEVELOP.md](docs/DEVELOP.md)。
问题走 Issues；报障请附 `python detect.py --list` 的输出，它不含会话内容。

## 许可

MIT © 2026 Yokos，见 [LICENSE](LICENSE)。
