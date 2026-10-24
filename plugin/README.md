# dsh-guard-ui

DSH NEXT 的界面开关插件：在会话头部「费用明细」左边放一个「防崩守护」按钮，
控制 `dsh-guard` 的后台守护进程。

## 它做什么

| 外观 | 含义 |
|---|---|
| 绿点「防崩守护 开」 | 守护在跑，心跳正常 |
| 橙点「防崩守护 待启」 | 开关开着但守护没跑（点一下拉起，也会自动补拉） |
| 灰点「防崩守护 关」 | 你关了；守护不扫描，只留心跳 |
| 红点「防崩守护 ?」 | 连不上宿主桥（插件没加载成功），守护本身不受影响 |

点一下 = 改 `state/control.json` 的 `enabled`。**只启停扫描与通知**，不改配置、不杀进程、不重启 DSH。

## 结构

- `lib/index.js` 宿主半边：起回环桥（`127.0.0.1:43891–43900` 退让，Origin 只放行本机与 `dsh-app://`）
- `lib/bridge.js`：`GET /state`、`POST /state`、`POST /diag`
- `lib/client.js`：`window.__ModuleLoader__.load` + `ctx.slots.inject/register`
- `lib/core.js`：状态文件与端口常量

## 安装（三件事，缺一不可）

```bat
:: 1) 复制到 DSH 的 local-plugins
xcopy /E /I /Y plugin "%DSH_HOME%\local-plugins\dsh-guard-ui"

:: 2) profile 的 package.json：加依赖 + 加进 dsh.profile.bundles
::    "@local/dsh-guard-ui": "link:<DSH_HOME>/local-plugins/dsh-guard-ui"

:: 3) 建 node_modules 软链（DSH 从这里解析 bundle，只写 link: 不够）
mklink /J "%DSH_HOME%\profiles\desktop\node_modules\@local\dsh-guard-ui" "%DSH_HOME%\local-plugins\dsh-guard-ui"
```

还有一条**代码级**要求：客户端模块必须导出 `inject`，否则 `ctx.slots` 取不到：

```js
var inject = ["slots"];
module.exports = { apply: apply, inject: inject };
```

漏了的表现是 `cannot get property "slots" without inject`——插槽全失败，**不报错、不崩溃、按钮就是不出现**。

## 自检

```bat
node scripts/selftest.mjs
```

6 项：GET / POST / 非 JSON 415 / 外站 Origin 403 / `dsh-app://` 放行 / 拉起守护。

## 排查

```bat
:: 桥在听吗
netstat -ano | findstr 43891
:: 客户端跑了吗（有 apply / inject-fired 就说明在跑）
type "%DSH_HOME%\..\..\AI\Projects\dsh-guard\state\client-diag.jsonl"
```

更系统的排查用主项目的 `diag.cmd`。
