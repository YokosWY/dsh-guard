# 结构

```
dsh-guard/          ← 仓库名；本地目录叫什么无所谓
  paths.py            本机路径解析（DSH home / python / pythonw），所有写死路径都收在这里
  detect.py           判定器：读 DSH 日志 + Windows 事件，把每次启动分类
  preflight.py        只读体检：BOM / JSON / YAML / link 目标 / 软链 / 端口 / 漂移
  lkg.py              配置备份仓：快照 / 基线 / 还原（三查 + 原子写 + 写后校验）
  sessions.py         会话备份：硬链接增量
  guard.py            常驻守护：定时扫描 + 通知 + 现场留存
  rescue.py           救援编排：体检 → 还原 / 安全模式
  plugin/             界面开关插件（宿主桥 + 客户端按钮）
  tools/              诊断、演练、报告生成、计划任务用的脚本
  tests/              单测 + 回放
  cmd 入口            救援.cmd / 自检.cmd / verify-safe-mode*.cmd / 发缺陷报告.cmd
  docs/               安装、结构、事件口径
  reports/            生成的报告（findings / bug / replay / safe-mode）
```

## 数据流

```
DSH NEXT
  ├─ logs/desktop-next-*.jsonl ──┐
  ├─ logs/desktop-next.log       ├─▶ detect.py ──▶ 分类结果
  └─ Windows System 事件 ────────┘        │
                                          ├─▶ guard.py（新事故 → incidents.jsonl + 气泡 + 现场）
                                          └─▶ replay.py（回放校验判定器）

preflight.py ──▶ rescue.py ──▶ lkg.py（还原）或 --next-safe-mode（兜底）

plugin（宿主桥 127.0.0.1:43891）◀──▶ state/control.json ◀── guard.py（每轮读一次）
```

## 关键口径（改代码前先看）

1. **按 `runId` 聚合，不按日期**：日志按 UTC 分卷，跨午夜会把一次启动拆到两个文件。
2. **`run.end` 不代表正常退出**：关机杀进程也会写；反过来很多失败 run 也有。
3. **关机序列**：杀进程必然发生在 `1074 发起 → 6006 日志停止` 之间。落在序列内才算关机导致；先崩、几十秒后用户才关机，那是真崩。
4. **写配置三保险**：无 DSH 进程 + 目标文件 mtime 10 秒未变 + 拿锁；同目录 temp → fsync → `os.replace`；写后重算 sha256，不符整批回滚。
5. **守护必须 `cmd /c start` 启动**：直接 spawn 会让它挂在 DSH 进程树里，DSH 一退出就被带走。

## 为什么不直接用 DSH 自带的 `recovery/`

`recovery\<ts>\snapshot.json` 在 Host ready 后约 0.3 秒就写 `healthy: true`，而实测存在
「写 healthy 快照后 0.55 秒就 runtime.failed」的案例——它会把即将崩的配置标成好份。
所以备份仓自建，两边互不覆盖。
