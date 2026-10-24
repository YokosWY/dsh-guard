# 开发约定

改代码前先读这页。下面每条都是实际踩过的，违反会得到「不报错、不崩溃、就是不生效」。

## 1. 日志口径（三条，写死在 detect.py）

| 口径 | 反例 | 正确做法 |
|---|---|---|
| 按 `runId` 聚合 | 按本地日期读「今天的日志」 | 日志按 UTC 分卷，跨午夜会把一次启动拆到两个文件 |
| 不用 `run.end` 判正常退出 | `has_run_end → ok` | 关机杀进程也会写 `run.end`；很多失败 run 也有 |
| 判「非崩」看关机序列 | 固定 ±120 秒窗口 | 杀进程必然在 `1074 发起 → 6006 日志停止` 之间；先崩、几十秒后关机是真崩 |

`unclean-unknown` 是「三类证据都没有」的兜底类，不允许猜死因，只允许留现场。

## 2. 写 DSH 配置的六道保险（lkg.py）

1. 三查：无 `DSH NEXT.exe` 进程、目标文件 mtime 10 秒未变、拿到 `.lock`
2. 同目录 temp → `fsync` → `os.replace`
3. 写后重算 sha256，不符整批回滚
4. 原文件先进 `quarantine/<ts>/`
5. 还原前自动打 `pre-restore` 快照
6. 每个动作写 `logs/incidents.jsonl`

差一道就别合。

## 3. 界面插件：五件事，缺一件静默失效

| # | 要求 | 漏了的表现 |
|---|---|---|
| 1 | profile `package.json` 写 `link:` 依赖 | — |
| 2 | profile `dsh.profile.bundles` 加入包名 | 插件不加载 |
| 3 | `profiles/desktop/node_modules/@local/<pkg>` 有 softlink/junction | `cordis.yml` 里没有该条目，无报错、不崩溃 |
| 4 | 插件 `package.json` 带 `dsh.client.platform = "web"` 与 `immediately` | 客户端半边不加载 |
| 5 | 客户端模块导出 `inject = ["slots"]` | `cannot get property "slots" without inject`，插槽全失败 |

第 5 条的 `ctx` 是 Cordis 服务代理，不声明就拿不到 `ctx.slots`。

## 4. 客户端常见形状错误

- `discover()` 命中缓存要返回 `{port}`，不能返回端口数字；调用方按对象取字段
- 插槽注册只做一次。重复注册会被拒，日志刷一屏 `slot ... already`
- 头部插槽是 `conversation.session.header.actions`（order 负数靠右），底部是 `conversation.composer.dock`，输入框右侧是 `conversation.input.right`

## 5. 脚本编码（三种文件三种规矩）

| 类型 | 编码 | 原因 |
|---|---|---|
| `.cmd` | ASCII only | cmd 按 OEM 码页解析批处理，中文会乱码或吃掉后续字符 |
| `.ps1` | UTF-8 **with BOM** | Windows PowerShell 5.1 无 BOM 时按 ANSI 读，中文引号错位直接解析失败 |
| `.json` / `.yml` | UTF-8 **without BOM** | 带 BOM 会让 DSH 解析 JSON 直接失败 |

输出统一 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`：被重定向时 Windows 会用 cp936，`✅` 这类字符会抛 `UnicodeEncodeError`。

## 6. 守护进程的启动方式

必须 `cmd /c start "" <pythonw> guard.py`。直接 `spawn` 会让它挂在 DSH 的进程树里，DSH 一退出就被带走，日志里连退出记录都没有。

## 7. 路径与配置

- 所有本机路径走 `paths.py`：`DSH_HOME` → `config.json` → 自动探测
- 自动探测按打分取最高：`logs/desktop-next*.jsonl` 是 DSH NEXT 的强特征；只看 `profiles/desktop/package.json` 会误选官方 `.dsh`
- 新增模块不要直接写 `Path(r"D:\...")`；测试里用 `tmp_path` 或 monkeypatch 模块常量

## 8. 测试与回放

```bat
自检.cmd
```

等于：`test_lkg` / `test_preflight` / `test_guard` / `test_sessions` / `test_rescue_e2e` + `replay.py` + `preflight.py`。

- 单测不读真机状态：会被机器现状影响的门槛（如基线健康门槛）用 monkeypatch 替掉
- `replay.py` 的期望标签由 `build_expected.py` 独立生成（判定逻辑独立，只有 `unclean-unknown` 的
  `category=silent` 是共享的词表约定）；一致只证明实现没跑偏，不证明规则对
- 安全模式与会话还原各有一份演练脚本：`tools/verify_safe_mode.py`、`tools/drill_sessions.py`

## 9. 提交前检查

1. `自检.cmd` 全绿
2. `python preflight.py` 无 FAIL
3. 没有本机绝对路径：`git ls-files | xargs grep -nE "D:\\\\AI\\\\|C:\\\\Users\\\\"` 应为空
4. 没有凭据：`git grep -nE "sk-|ghp_|AKIA|Bearer "` 应为空
5. `.cmd` 里没有非 ASCII
6. `.ps1` 有 BOM，`.json/.yml` 无 BOM
7. 新功能在 README 的特性表里有一行，带可核对项（数字/命令）

## 10. 目录约定

| 路径 | 放什么 |
|---|---|
| 根 | 用户直接调用的入口：`detect.py`、`lkg.py`、`rescue.py`、`自检.cmd`、`救援.cmd` |
| `tools/` | 诊断、演练、报告生成、计划任务脚本 |
| `tests/` | 单测与回放；不产生仓库外的副作用 |
| `docs/` | 安装、结构、开发约定 |
| `plugin/` | 界面插件；安装时复制到 DSH 的 `local-plugins` |
| `state/` `logs/` `lkg/` `sessions-backup/` `quarantine/` | 运行时产物，全部 gitignore |

## 11. 两套 DSH 的配置差异（迁移时注意）

同一个 profile 配置在两套之间复制时，注意 `@deepseek-ai/dsh-tool-subagent` 的字段：

| 版本 | 支持 `toolFilter` / `persona` | 现象 |
|---|---|---|
| NEXT 的 `0.2.1-alpha.2` | 支持 | 正常加载 |
| 官方版的 `0.2.1-rc.2` | **拒绝**，报 `tool-subagent: <field> is no longer supported` | 新建会话直接失败 |

迁移配置到官方版之前，把 preset 里 `toolFilter` / `persona` 整块删掉，否则会卡在「新建会话失败」。
查法：`grep -n "toolFilter\|persona:" profiles/desktop/cordis.patch.yml`。

## 12. 输出与漂移口径

- `guard.py --status` / `--report`：巡检视图，读的是各目标的最新日志 + `state/scan.json`
- `detect.py --csv`：一行一次启动，列固定为 `target,run_id,start,duration_s,kind,category,repair,boot,shutdown,host`
- `preflight.py` 的漂移**排除 `storages/`**：那是运行期活写（插件状态、会话缓存），天天变，
  算进去就永远是「有漂移」，等于没检查。要判断配置有没有被改坏，看 `profiles/` 与 `local-plugins/`
