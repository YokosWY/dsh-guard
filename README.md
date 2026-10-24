# dsh-guard · 救生艇

给 **DSH NEXT**（DeepSeek Harness 桌面版）做的外部看守，像救生艇：崩了能上船，先活着。

DSH 崩了以后，它自己的「恢复」界面有时候是坏的（本项目实测到过恢复页 `ERR_FAILED`），
这时你既不知道死在哪，也进不去。dsh-guard 解决三件事：

1. **判死** —— 读 DSH 的结构化日志 + Windows 关机事件，把每次启动分类，告诉你这次到底是崩溃、是关机、还是正常退出
2. **救回来** —— 配置类崩溃从自建基线还原；配置无关的崩溃进 DSH 自带的安全模式先干活
3. **别让它悄悄坏** —— 常驻守护记录每次事故，界面「费用明细」旁边有个开关可以随时启停

> 不修改 DSH 本体，不改它的安装包，不碰它的配置目录（还原时除外，且有六道保险）。

## 能力

| 能力 | 入口 | 说明 |
|---|---|---|
| 判死 + 分类 | `python detect.py --list` | 7 类结局：ok / shutdown / quit-forced / boot-fail / crash / unclean-unknown / in-progress |
| 启动前体检 | `python preflight.py` | 只读：BOM / JSON / YAML / `link:` 目标 / 软链 / 端口 / 基线漂移 |
| 一键救援 | `救援.cmd` | 体检 → 配置类还原 → 否则安全模式 |
| 配置备份 | `lkg.py` | 快照 / 基线 / 还原；写前三查 + 原子写 + 写后校验 + 自动回滚 |
| 会话备份 | `sessions.py` | 硬链接增量（第二份新增 0.00 MB） |
| 常驻守护 | `guard.py` | 120 秒一轮，只记录 + 通知；不自动改配置、不杀进程 |
| 界面开关 | `plugin/` | 会话头部「费用明细」左边，绿/橙/灰/红四态 |
| 一键诊断 | `diag.cmd` | 五段：桥 / bundle / 客户端 / 守护 / 自启与任务 |

## 快速开始

见 [`docs/INSTALL.md`](docs/INSTALL.md)。三步：

```bat
copy config.example.json config.json     :: 改 home 指向你的 DSH 数据目录
python detect.py --list                  :: 只读，先看它怎么判
python preflight.py                      :: 只读体检
```

要能救，再建基线：

```bat
python lkg.py snapshot --reason first
python lkg.py promote <id> --yes
```

## 判定口径

| kind | 含义 | 算不算事故 |
|---|---|---|
| `ok` | 有 `run.end` | 否 |
| `shutdown` | 落在关机序列内（1074 发起 → 6006 停日志） | 否 |
| `quit-forced` | 用户关窗、host 卡 >5 秒被强杀 | 是缺陷，不是崩溃 |
| `boot-fail` | 没到 `Host ready` 就失败 | 是 |
| `crash` | 到过 `Host ready` 后失败 | 是 |
| `unclean-unknown` | 无 `run.end`、无 `runtime.failed`、无关机事件 | 交人工，不猜 |
| `in-progress` | 还在跑 | — |

三条容易踩错的前提，代码里都写死了：

1. **日志按 `runId` 聚合**，不按日期——日志是 UTC 分卷，跨午夜会把一次启动拆成两个文件
2. **`run.end` 不代表正常退出**——关机杀进程也会写；反过来很多失败 run 也有
3. **判「非崩」看关机序列**，不看进程还在不在

## 实测数据

8 天 78 次启动：ok 26 / shutdown 13 / quit-forced 8 / boot-fail 19 / crash 3 / unclean-unknown 8 / in-progress 1。

- 判定器回放：漏报 0、误报 0
- **能靠配置备份救的只有 3 次**——其余是 DSH 本体问题（18 次壳页 `ERR_FAILED`，0.3–0.7 秒自退，profile 完全没加载）
- 详细分析与缺陷证据留在本机 `reports/`，不入库（含运行数据）

## 已知限制

- 18 次壳页加载失败属 DSH 本体，配置层无解，本工具只能出证据
- `unclean-unknown` 只留现场（事件尾巴 + 文本日志 + 进程快照），不猜死因
- 计划任务只在**当前用户登录后**生效；注销/关机期间什么都不做
- 备份不含 DSH 的 `recovery/`（那是应用自己的，本项目刻意不用：它会把即将崩的配置标成 healthy）

## 目录

```
paths.py      本机路径解析（DSH home / python / pythonw）
detect.py     判定器
preflight.py  只读体检
lkg.py        配置备份仓
sessions.py   会话备份
guard.py      常驻守护
rescue.py     救援编排
plugin/       界面开关插件
tools/        诊断 / 演练 / 报告 / 计划任务脚本
tests/        38 项单测 + 78 次回放
docs/         安装、结构、口径
```

更细的模块关系见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 测试

```bat
自检.cmd
```

等于：5 个测试文件 + 回放 + 当前配置体检。

## 许可

MIT，见 [LICENSE](LICENSE)。
