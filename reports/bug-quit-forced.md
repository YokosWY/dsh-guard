# 缺陷报告：关窗时 host.stop 超时，被强杀并记成异常退出

- 版本：DSH NEXT `2.0.18-next`（`D:\AI\DSH-NEXT\app\DSH NEXT.exe`）
- 生成时间：2026-10-24 14:12
- 样本：10 次（取自 2026-10-17 ~ 2026-10-24，同期共 84 次启动）

## 现象

用户正常关闭主窗口后，应用开始 `shutdown.start`；`host.stop` 阶段卡住超过 5 秒，日志出现 `Shutdown exceeded five seconds`（事件 `shutdown.timeout`），随后 `run.forced-exit`。
下一次启动出现 `run.previous-unclean`，也就是把「正常关窗」记录成了一次异常退出。

## 影响

1. 每次正常退出都被计成 unpublished crash，用户看到「上次异常退出」类提示
2. 与真实崩溃混在一起，排障时要靠日志逐条排除
3. 强制结束进程时可能有未落盘的状态（本次样本里未观察到数据丢失，但风险存在）

## 样本

| # | 启动时刻 (UTC) | 会话时长 | shutdown.start → 强杀 | 之后下次启动报 unclean |
|---|---|---|---|---|
| 1 | 10-18 12:04:56 | 105 分钟 | 5s | 是 |
| 2 | 10-21 04:32:56 | 537 分钟 | 5s | 是 |
| 3 | 10-22 04:22:50 | 64 分钟 | 5s | 是 |
| 4 | 10-23 17:50:30 | 6 分钟 | 5s | 是 |
| 5 | 10-23 17:57:38 | 3 分钟 | 5s | 是 |
| 6 | 10-23 18:03:18 | 108 秒 | 5s | 是 |
| 7 | 10-23 18:05:05 | 99 秒 | 5s | 是 |
| 8 | 10-23 18:06:54 | 82 秒 | 5s | 是 |
| 9 | 10-24 04:38:49 | 26 分钟 | 5s | 是 |
| 10 | 10-24 05:05:32 | 44 分钟 | 5s | 是 |

## 原始日志（首例）

```json
{"time":"2026-10-18T13:49:58.337Z","event":"shutdown.start","pid":48004}
{"time":"2026-10-18T13:49:58.369Z","event":"shutdown.start","pid":29036}
{"time":"2026-10-18T13:50:03.349Z","event":"shutdown.timeout","pid":48004}
{"time":"2026-10-18T13:50:03.352Z","event":"run.forced-exit","pid":48004}
```

## 复现步骤

1. 打开 DSH NEXT，正常使用一段时间（与时长无关，短会话也出现）
2. 点关闭窗口
3. 观察 `%APPDATA%\..\` 下 home 的 `logs\desktop-next.log`：应出现 `Shutdown exceeded five seconds`
4. 重新打开，观察日志出现 `run.previous-unclean`

## 猜测方向（供参考，未验证）

- `host.stop` 等待某个子进程（MCP 连接 / 子代理 / 终端会话）未按时退出
- 每次强杀时日志出现 `pendingOperations: 2`，可能与未完成的工具调用有关

## 附加材料

- 判定口径：关窗导致的 `run.forced-exit` 不计为崩溃，已从崩溃统计里排除
- 数据来源：`D:\AI\DSH-NEXT\logs\desktop-next-*.jsonl`（结构化事件）
