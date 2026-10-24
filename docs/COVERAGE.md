# 覆盖范围：哪些崩法能判，哪些判不了

这张表决定你能不能信任 `detect.py` 的结论。判断顺序：先看「已覆盖」，再看「判不了」——判不了的会明确落到 `unclean-unknown`，不会被硬凑成某个原因。

## 已覆盖（有日志证据）

| 崩法 | 我们靠什么判 | 分类 |
|---|---|---|
| 壳页加载失败 | `runtime.failed: ERR_FAILED … dsh-app://app/` | `shell-load` |
| 插件 import 失败 | 同上，消息含 `failed to import` / `Cannot find` | `plugin-import` |
| 配置被写坏 | 消息含 BOM / JSON 解析失败 | `config-broken` |
| Host 进程退出 | `host exited with <code>` | `host-exit` |
| 渲染进程 / GPU 进程崩溃 | `child-process-gone` 且 `type=Renderer`/`GPU` | `renderer-gone`（能力受损） |
| 子进程 OOM | `child-process-gone` 且 `reason=oom` | `oom` |
| 网络服务崩溃 | `child-process-gone` 且 service 含 `NetworkService` | `network-gone`（能力受损） |
| 连环崩 | 10 分钟内 ≥3 次异常启动 | 标 `crash_loop` |
| 关窗超时被强杀 | `shutdown.timeout` → `run.forced-exit` | `quit-forced` |
| Host 关机没走完 | host 层 `shutdown.start` 无对应 `shutdown.complete` | 备注 |
| 正常退出 / 关机杀进程 | `run.end`、关机序列（1074 → 6006） | `ok` / `shutdown` |

`renderer-gone` / `network-gone` / `oom` 是**降级**而非崩溃：run 可能正常结束，但那次会话里 UI 白过、网络断过。它们在统计里单列（`另有: 能力受损 N 次`），不混进崩溃数。

## 判不了（没进日志，或日志里没有可区分特征）

| 崩法 | 为什么判不了 | 例子 |
|---|---|---|
| GPU 进程初始化失败直接退出 | 双击后约 1 秒退出，**应用不写任何日志**，连 `run.start` 都没有 | [FAQ 927](https://dsh-ai.com/faq/927/) |
| 升级中断导致启动崩溃 | 宿主未完成 graceful teardown，pending 不落地；日志停在安装阶段 | [FAQ 1093](https://dsh-ai.com/faq/1093/) |
| 插件服务名冲突致白屏 | 应用完全正常，只是 UI 渲染出错，没有崩溃事件 | [FAQ 74](https://dsh-ai.com/faq/74/) |
| Host 活着但服务不重启 | Electron 壳活着，UI 无提示；要持续探测 HTTP 才能发现 | [FAQ 1101](https://dsh-ai.com/faq/1101/) |
| SIGTERM/SIGHUP 静默退出 | 被当成干净退出，无持久标记 | [FAQ 1094](https://dsh-ai.com/faq/1094/) |
| 会话格式迁移 SIGABRT | 退出码 134，日志可能来不及刷盘 | [FAQ 933](https://dsh-ai.com/faq/933/) |
| 低权限标签残留 | 工作区被封条继承，崩溃发生在 Electron 层 | [FAQ 1076](https://dsh-ai.com/faq/1076/) |
| 子代理结算 fatal | 发生在 in-process，只有子会话日志有线索 | [FAQ 942](https://dsh-ai.com/faq/942/) |
| Node 堆溢出（宿主 OOM） | 只在 stderr 出现 `heap out of memory`，结构化日志可能为空 | [FAQ 222](https://dsh-ai.com/faq/222/) |
| 应用自己把配置重置 | 启动失败触发 filesystem 恢复，**重写 `dsh.profile.bundles` 并丢弃补丁条目** | [FAQ 1126](https://dsh-ai.com/faq/1126/) |

最后一条尤其要注意：DSH 会自愈式重写你的 profile。所以本项目的备份仓必须自己留一份，不能只依赖 DSH 的 `recovery/`。


## 环境异常（不是崩溃，但会话会半残）

这些不会让 run 失败，所以不会进崩溃统计；以前完全看不见。

| 签名 | 判据 | 后果 |
|---|---|---|
| `port-in-use` | 日志出现 `EADDRINUSE` 且本轮没监听成功 | 内置网关起不来（本机 40/89 次启动命中 `openai-gateway` 端口 8326） |
| `port-conflict-retried` | 出现 `EADDRINUSE` 但随后 `已监听` | 上一实例没退干净的短暂冲突，不算受损 |
| `econnreset` | `ECONNRESET` | 网络请求被重置，日志里是 npm registry 重试 |
| `file-locked` | `EPERM` / `EBUSY` | 写入被拦（杀软或文件被占用），状态可能只落一半 |
| `heap-oom` | `heap out of memory` | 宿主堆溢出，进程可能直接消失 |
| `module-missing` | `ERR_MODULE_NOT_FOUND` | 插件依赖缺失，插件起不来 |
| `dll-init-failed` | `0xC0000142` / `STATUS_DLL_INIT_FAILED` | 受限沙箱子进程初始化失败，shell 全废 |
| `disk-full` | `ENOSPC` | 磁盘满 |

另外统计「工具失败码」，其中三类会提示环境坏了（≥3 次/轮才记）：

| failureCode | 含义 |
|---|---|
| `UNKNOWN_TOOL` | 工具在会话中途消失，多为运行中改 profile 或插件被卸载 |
| `FS_SANDBOX_DENIED` | 被文件沙箱拒绝 |
| `WEB_PROVIDER_ERROR` | 联网工具失败（网络或供应商侧） |

`UNKNOWN_TOOL` 对应上游已知问题：运行中编辑 `cordis.patch.yml` 会把实时会话的大部分工具移除
（[FAQ 932](https://dsh-ai.com/faq/932/)）。本机 32 次命中。


### 插件没激活（`plugin-inactive`）

Cordis 激活失败的插件不会让进程崩，但相关功能整段消失。判据是 stderr 里的
`N entry did not activate` 加上紧随的错误行 `<插件名> (<包名>): <Error>: <消息>`。

本机 89 次启动里命中 57 次，全部是同一个插件：

| 插件 | 错误 | 影响 |
|---|---|---|
| `dsh-at-file` | `TypeError: ctx.settings.register is not a function` | `@` 引用文件的功能不可用（**已用 `tools/patch_at_file.py` 打本地补丁：宿主无 `register()` 时降级为内存设置**） |

官方版那边命中一次，是另一种典型：

| 插件 | 错误 | 说明 |
|---|---|---|
| `agnes-image` | `Error: cannot get property "tools" without inject` | 插件没导出 `inject`，Cordis 服务代理取不到 `ctx.tools` |

后一种错误本项目也踩过：客户端模块必须 `exports.inject = ["slots"]`，宿主模块要写 `export const inject = [...]`。
检测器现在会直接给出这条提示。

还有一类是「重复注册」：`file-upload: Agent resolver is already registered`，对应上游
[FAQ 1031](https://dsh-ai.com/faq/1031/)（运行中写 `cordis.patch.yml` 触发重载，`session-controller` 重建时重复注册）。

## 上报给上游时怎么用

`child-process-gone` 的 reason 取值来自 Electron 文档：`crashed`、`oom`、`launch-failed`、`integrity-failure`（Windows 代码完整性检查失败）、`killed`（[app 事件](https://www.electronjs.org/docs/latest/api/app)、[RenderProcessGoneDetails](https://electronjs.org/docs/latest/api/structures/render-process-gone-details)）。

`integrity-failure` 和 `launch-failed` 目前在本机样本里没有出现，但分类已经就位；出现时会落在 `renderer-gone` 并带上 reason。

## 已知的统计口径

- 18 次壳页加载失败是同期最多的失败类，属应用本体，配置层无解
- 13 次「关机杀进程」不算崩溃：判据是杀进程时刻落在 1074 发起 → 6006 日志停止之间
- `unclean-unknown` 不猜原因，只留现场（事件尾巴 + 文本日志 + 进程快照）
