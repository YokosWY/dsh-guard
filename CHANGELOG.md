# CHANGELOG

## 0.2.1 — 2026-10-24

打磨轮：把命令行做完整，修掉几个会静默错的行为。

- `preflight.py` 支持 `--home` / `--label` / `--help`；第二套 DSH 也能单独体检
- `rescue.py` 支持 `--home` / `--label` / `--help`；按目标取对应基线，不再只看默认仓
- `detect.py --home` 指向不存在的目录时直接报错（之前会静默输出 0 条）
- `lkg.py --home` 真的生效了（之前只改模块变量，快照仍按 config.json 的 home 走）
- `lkg.py` 空快照给出明确错误，而不是 `FileNotFoundError`
- 进程识别覆盖两套：`DSH NEXT.exe` 与 `DeepSeek Harness.exe`，避免对着运行中的实例做还原
- 新增 `.editorconfig`：`.cmd` 用 ASCII+CRLF，`.ps1` 用 UTF-8 BOM，JSON/YAML 两空格
- 测试 43 → 48 项（preflight +3、lkg +2）

## 0.2.0 — 2026-10-24

多实例支持：同一台机器上的 DSH NEXT 与官方版可以一起盯。

- `config.json` 增加 `targets`；`detect.py --all` / `--home` 逐目标判定
- `guard.py` 每轮遍历所有目标，`scan.json` 按 target 分开存，新目标首次跑只建基线不通知
- `lkg.py` / `sessions.py` 增加 `--label`、`--home`：第一个目标用默认目录，其余按名字分目录
- 新增 `tools/backup_all.py`：每日任务改为逐个目标备份（配置快照 + 会话增量）
- 插件可装到官方版：0.2.1-rc.2 的日志事件与头部插槽与 NEXT 一致，实测可加载
- 测试 38 → 43 项（新增 `tests/test_multi_target.py` 5 项）

## 0.1.0 — 2026-10-24

首个可用版本。全部能力都在这台机器上实测过，不是只写了代码。

### 判定

- `detect.py`：按 `runId` 聚合 DSH NEXT 的 UTC 分卷日志，输出 7 类结局
  （ok / shutdown / quit-forced / boot-fail / crash / unclean-unknown / in-progress）
- 三闸门：开机时间、关机序列（1074 发起 → 6006 停止）、host 是否起过
- 回放 78 次历史启动：漏报 0、误报 0

### 体检与救援

- `preflight.py`：只读体检 BOM / JSON / YAML（放行 `!!js`）/ `link:` 目标 / node_modules 软链 / 端口 / 基线漂移
- `rescue.py`：配置类问题走基线还原（三查 + 原子写 + 写后校验 + 自动回滚），其它走安全模式

### 备份

- `lkg.py`：配置快照（57 文件 / 0.82 MB），硬编码本机路径已收拢到 `paths.py`，可用 `config.json` 覆盖
- `sessions.py`：会话增量备份，未变的文件走硬链接（第二份新增 0.00 MB）
- 每日 12:40 自动：配置快照 + 会话增量

### 守护与界面

- `guard.py`：120 秒一轮，只记录 + 通知；`--ensure` 供看护任务用；`unclean-unknown` 会留现场文件
- 插件 `plugin/`：会话头部「费用明细」左侧的启停按钮，绿/橙/灰/红四态
- 自启：HKCU Run + 每 5 分钟看护任务（都不需要管理员）

### 验证

- 38 项单测 + 78 次回放
- 安全模式 `--next-safe-mode` 实机验证通过（2026-10-24 14:10）
- 会话还原实机演练（真实 52 MB 数据，沙箱内完成）

### 已知限制

- 18 次壳页加载失败属 DSH 本体，配置层无解
- `unclean-unknown` 只留现场，不猜死因
- 计划任务仅在当前用户登录后生效
