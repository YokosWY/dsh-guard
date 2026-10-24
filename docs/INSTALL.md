# 安装与部署

## 依赖

- Windows 10/11
- Python 3.11+（只用标准库；`psutil`、`PyYAML` 可选：没有就自动降级）
- DSH NEXT（本工具靠读它的日志工作，不修改它）

## 1. 放哪

下面用 `%PROJECT%` 代表你放这个项目的目录（例如 `D:	ools\dsh-guard`）。

```bat
git clone https://github.com/<you>/dsh-guard.git %PROJECT%
cd /d %PROJECT%
copy config.example.json config.json
```

编辑 `config.json`，至少确认 `home` 指向你的 DSH 数据目录（含 `logs\`、`profiles\`、`sessions\`）。
不写也行——`paths.py` 会自动探测（`DSH_HOME` 环境变量 → `config.json` → 常见安装位置）。

验证：

```bat
python paths.py
```

应该看到 `dsh_home` / `python` / `pythonw` 三项都对。

## 2. 先跑只读部分

```bat
python detect.py --list      :: 判死：把历史启动分类
python preflight.py          :: 体检当前配置（只读）
python diag.cmd              :: 五段健康检查
```

这一步不会改任何东西。

## 3. 建基线（要写自己的目录，不碰 DSH）

```bat
python lkg.py snapshot --reason first
python lkg.py promote <上面输出的id> --yes
```

`promote` 有健康门槛：最近一次崩溃之后要成功启动过 3 次，或当前已稳定 6 小时。
第一次装还没有历史时，可以 `--force` 越过。

## 4. 装界面开关（可选）

插件在 `plugin\`。它需要三件事同时成立，缺一个就会**静默失效、不报错**：

```bat
:: ① 复制/软链到 DSH 的 local-plugins
mklink /J "%DSH_HOME%\local-plugins\dsh-guard-ui" "%PROJECT%\plugin"

:: ② 在 profile 的 package.json 里加依赖
::    "@local/dsh-guard-ui": "link:<DSH_HOME>/local-plugins/dsh-guard-ui"
::    并把 "@local/dsh-guard-ui" 加进 dsh.profile.bundles

:: ③ 建 node_modules 软链（DSH 从这里解析 bundle）
mklink /J "%DSH_HOME%\profiles\desktop\node_modules\@local\dsh-guard-ui" "%DSH_HOME%\local-plugins\dsh-guard-ui"
```

改完必须**完全重启 DSH**（不是刷新页面）才会加载。

验证：

```bat
python preflight.py          :: 应显示 32 个 bundle 全部可解析
python diag.cmd              :: 第 1、2、3 段全绿
```

按钮位置：会话头部「费用明细」左边（插槽 `conversation.session.header.actions`，order -4）。

## 5. 常驻守护（可选）

```bat
:: 登录自启
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v DSH-Guard /t REG_SZ ^
 /d "\"<pythonw路径>\" \"%PROJECT%\guard.py\"" /f

:: 两个计划任务（当前用户，不需要管理员）
schtasks /create /tn "DSH-Guard-Daily-Snapshot" /tr "\"%PROJECT%\tools\daily-snapshot.cmd\"" /sc daily /st 12:40 /f
schtasks /create /tn "DSH-Guard-Ensure" /tr "\"%PROJECT%\tools\guard-ensure.cmd\"" /sc minute /mo 5 /f
```

撤销：

```bat
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v DSH-Guard /f
schtasks /delete /tn "DSH-Guard-Daily-Snapshot" /f
schtasks /delete /tn "DSH-Guard-Ensure" /f
```

## 6. 自检

```bat
python tests\test_lkg.py
python tests\test_preflight.py
python tests\test_guard.py
python tests\test_sessions.py
python tests\test_rescue_e2e.py
python tests\replay.py
```

或双击 `自检.cmd` 一次跑完。

## 卸载

删目录即可。额外清理：

1. `reg delete "HKCU\...\Run" /v DSH-Guard /f`
2. `schtasks /delete /tn "DSH-Guard-Ensure" /f`、`... "DSH-Guard-Daily-Snapshot" /f`
3. 从 profile `package.json` 里移除 `@local/dsh-guard-ui`，并删掉两个 junction
4. 结束残留的 `pythonw.exe`（守护进程）
