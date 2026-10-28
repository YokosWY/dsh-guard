/**
 * dsh-guard-ui — 客户端半边
 *
 * 在会话头部（`conversation.session.header.actions`，也就是「费用明细」那一排）放一个启停按钮：
 *   绿点  守护运行中
 *   橙点  已开启但守护没在跑（多半是 pythonw 没起来）
 *   灰点  已关闭
 *
 * ── 四条纪律 ───────────────────────────────────────────────────────────
 *  1. **fail-open**：桥不通、插槽注册失败，只让按钮变灰或消失，绝不连累头部。
 *  2. **不碰官方 DOM**：只用插槽 API 注册自己的组件。
 *  3. **只读项目状态目录**：经宿主桥读写 `D:\AI\Projects\dsh-guard\state\`，不碰 DSH 的配置。
 *  4. **状态只信文件**：按钮显示的是 control.json + heartbeat.json 的真实状态，不是本地猜测。
 */
/*
 * id 必须是包名（含 @scope）：dsh-client-modules 用 package name 当模块身份，
 * 写成短名会 duplicate factory registration → web boot 失败。
 */
window.__ModuleLoader__.load({
  id: "dsh-guard-ui",
  factory: function (require) {
    // 模块一执行就在这里打一枪（放在 require("react") 之前，连 React 挂掉都能报出来）
    try {
      diag("module-load", { href: String(location.href).slice(0, 120) });
    } catch {
      /* 忽略 */
    }

    var module = { exports: {} };
    var exports = module.exports;

    var React = require("react");
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useCallback = React.useCallback;
    var createElement = React.createElement;

    var PORT_BASE = 43891;
    var PORT_SPAN = 10; // 与宿主一致：43891..43900
    var PORT_KEY = "dsh-guard-ui-port";
    var PLUGIN_TAG = "dsh-guard-ui";
    var POLL_MS = 5000;
    /* 诊断期：每个插槽都试一遍，标签带缩写，方便一眼看出是哪个槽渲染的。
       确认后再收窄成一条（见 SLOT_FINAL）。 */
    /* 2026-10-24 实测：`conversation.session.header.actions` 就是「费用明细」那一排，
       注册后紧挨在它左边。其余候选（utilities / corner / composer.dock / input.right）
       在本版本里没有触发，已撤掉，只留这一个。 */
    var SLOT_CANDIDATES = [
      ["conversation.session.header.actions", -4, ""],
    ];
    /* 必须在导出里声明：Cordis 的 ctx 是服务代理，不声明 inject 就取不到 ctx.slots
       （报错原文：cannot get property "slots" without inject） */
    var inject = ["slots"];
    var autoSpawned = false;   // 自动补拉去重
    var diagPort = null;

    /** 埋点：POST /diag。首次调用时先探测端口；失败一律静默。 */
    function diag(event, extra) {
      try {
        var payload = JSON.stringify({ event: event, extra: extra || null });
        var send = function (port) {
          try {
            fetch("http://127.0.0.1:" + port + "/diag", {
              method: "POST",
              credentials: "omit",
              keepalive: true,
              headers: { "content-type": "application/json" },
              body: payload,
            }).catch(function () {});
          } catch {
            /* 忽略 */
          }
        };
        var known = diagPort || cachedPort;
        if (known) {
          send(known);
          return;
        }
        discover().then(function (hit) {
          if (hit) {
            diagPort = hit.port;
            send(hit.port);
          }
        }).catch(function () {});
      } catch {
        /* 埋点绝不能影响插件 */
      }
    }

    /* ---------------------------------------------------------------- 桥 */

    var cachedPort = null;

    function readPortLocal() {
      try {
        var v = parseInt(window.localStorage.getItem(PORT_KEY) || "", 10);
        return Number.isFinite(v) && v >= PORT_BASE && v < PORT_BASE + PORT_SPAN ? v : null;
      } catch {
        return null;
      }
    }

    function savePort(port) {
      try {
        window.localStorage.setItem(PORT_KEY, String(port));
      } catch {
        /* 忽略 */
      }
    }

    function url(port, path) {
      return "http://127.0.0.1:" + port + path;
    }

    function probe(port) {
      return fetch(url(port, "/state?plugin=" + PLUGIN_TAG), {
        method: "GET",
        credentials: "omit",
        cache: "no-store",
      })
        .then(function (res) {
          if (!res.ok) return null;
          return res.json();
        })
        .then(function (data) {
          return data && data.plugin === PLUGIN_TAG ? data : null;
        })
        .catch(function () {
          return null;
        });
    }

    var discovering = null;

    /** 依次探测端口，认 `plugin` 标记，避免两套 DSH 抢 43891。
     *  注意：缓存命中时也必须返回 {port} 形状 —— 之前返回数字，害得 getState 拿到 undefined。 */
    function discover() {
      if (cachedPort) return Promise.resolve({ port: cachedPort, data: null });
      if (discovering) return discovering;
      var list = [];
      var local = readPortLocal();
      if (local) list.push(local);
      for (var i = 0; i < PORT_SPAN; i += 1) {
        var p = PORT_BASE + i;
        if (local !== p) list.push(p);
      }
      discovering = Promise.all(
        list.map(function (port) {
          return probe(port).then(function (data) {
            return data ? { port: port, data: data } : null;
          });
        }),
      ).then(function (results) {
        discovering = null;
        for (var j = 0; j < results.length; j += 1) {
          if (results[j]) {
            cachedPort = results[j].port;
            savePort(cachedPort);
            return results[j];
          }
        }
        return null;
      });
      return discovering;
    }

    function getState() {
      return discover().then(function (hit) {
        if (!hit) return null;
        return probe(hit.port).then(function (data) {
          return data || hit.data;
        });
      });
    }

    function setEnabled(enabled) {
      return discover().then(function (hit) {
        if (!hit) return null;
        return fetch(url(hit.port, "/state"), {
          method: "POST",
          credentials: "omit",
          headers: { "content-type": "application/json" },
          cache: "no-store",
          body: JSON.stringify({ enabled: enabled, reason: "用户从界面切换" }),
        })
          .then(function (res) {
            return res.ok ? res.json() : null;
          })
          .catch(function () {
            return null;
          });
      });
    }

    /* --------------------------------------------------------------- 样式 */

    var CSS = [
      ".dgu-pill{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 9px;",
      "border-radius:8px;border:1px solid rgba(127,127,127,.35);background:transparent;",
      "color:inherit;font-size:12px;line-height:1;cursor:pointer;opacity:.85;",
      "transition:opacity .15s ease,border-color .15s ease;white-space:nowrap}",
      ".dgu-pill:hover{opacity:1;border-color:rgba(127,127,127,.6)}",
      ".dgu-pill[disabled]{cursor:progress;opacity:.5}",
      ".dgu-dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto;background:#8a8a8a}",
      ".dgu-on .dgu-dot{background:#22c55e}",
      ".dgu-stale .dgu-dot{background:#f59e0b}",
      ".dgu-off .dgu-dot{background:#8a8a8a}",
      ".dgu-unknown .dgu-dot{background:#ef4444}",
      ".dgu-label{letter-spacing:.2px}",
    ].join("");

    var stylesInstalled = false;

    function installStyles() {
      if (stylesInstalled) return;
      try {
        if (document.getElementById("dgu-styles")) {
          stylesInstalled = true;
          return;
        }
        var el = document.createElement("style");
        el.id = "dgu-styles";
        el.textContent = CSS;
        document.head.appendChild(el);
        stylesInstalled = true;
      } catch {
        /* 插不进样式就退回默认外观 */
      }
    }

    /* --------------------------------------------------------------- 组件 */

    function tone(state) {
      if (!state) return "unknown";
      if (!state.enabled) return "off";
      return state.running ? "on" : "stale";
    }

    function label(state, tag) {
      var base;
      if (!state) base = "护航 ?";
      else if (!state.enabled) base = "护航 关";
      else base = state.running ? "护航 开" : "护航 待启";
      return tag ? base + "·" + tag : base;
    }

    function titleFor(state) {
      if (!state) {
        return "连不上 dsh-guard 宿主桥（插件可能没加载成功）。守护本身不受影响。";
      }
      var lines = [];
      lines.push(state.enabled ? "防护：已开启" : "防护：已关闭");
      if (state.enabled) {
        lines.push(
          state.running
            ? "守护进程：运行中" + (state.pid ? "（pid " + state.pid + "）" : "")
            : "守护进程：未运行（点一下会重新拉起）",
        );
      }
      if (state.heartbeat_age !== null && state.heartbeat_age !== undefined) {
        lines.push("心跳：" + state.heartbeat_age + " 秒前");
      }
      if (!state.guardExists) lines.push("⚠ 找不到 guard.py，可能还没装到项目目录");
      if (state.last && state.last.kind) {
        lines.push("最近一次记录：" + state.last.kind + (state.last.t ? " @ " + state.last.t : ""));
        if (state.last.detail) lines.push(state.last.detail);
      }
      if (state.reason) lines.push("备注：" + state.reason);
      lines.push("");
      lines.push("点一下切换开 / 关。关掉只是停守护，不动你的配置。");
      return lines.join("\n");
    }

    function GuardToggle(props) {
      var sessionId = props && props.sessionId;
      var tag = props && props.tag;
      var _a = useState(null);
      var state = _a[0];
      var setState = _a[1];
      var _b = useState(false);
      var busy = _b[0];
      var setBusy = _b[1];

      var refresh = useCallback(function () {
        getState().then(function (next) {
          setState(next);
        });
      }, []);

      useEffect(function () {
        var alive = true;
        var tick = function () {
          if (!alive) return;
          getState().then(function (next) {
            if (!alive) return;
            setState(next);
            // 开关是开的、但守护不在（比如 DSH 重启把它带走了）→ 自动补拉一次
            if (next && next.enabled && !next.running && !autoSpawned) {
              autoSpawned = true;
              setEnabled(true).then(function (data) {
                if (alive && data) setState(data);
                setTimeout(function () {
                  autoSpawned = false;   // 留个口子，避免永久锁死
                }, 30000);
              }).catch(function () {
                autoSpawned = false;
              });
            }
          });
        };
        tick();
        diag("mounted", { tag: tag, sessionId: sessionId || null });
        var timer = setInterval(tick, POLL_MS);
        return function () {
          alive = false;
          clearInterval(timer);
        };
      }, [sessionId]);

      var onClick = useCallback(function () {
        if (busy) return;
        setBusy(true);
        var next = state ? !state.enabled : true;
        setEnabled(next)
          .then(function (data) {
            if (data) setState(data);
          })
          .then(function () {
            setBusy(false);
            refresh();
          })
          .catch(function () {
            setBusy(false);
          });
      }, [busy, state, refresh]);

      var cls = "dgu-pill dgu-" + tone(state);
      return createElement(
        "button",
        {
          type: "button",
          className: cls,
          title: titleFor(state),
          onClick: onClick,
          disabled: busy,
          "aria-label": "护航开关",
          "data-state": tone(state),
        },
        createElement("span", { className: "dgu-dot", key: "dot" }),
        createElement("span", { className: "dgu-label", key: "label" }, label(state, tag)),
      );
    }

    /* ------------------------------------------------------------ 插槽注册 */

    function trySlot(ctx, name, order, tag) {
      try {
        diag("inject-call", { slot: name, tag: tag });
        ctx.slots.inject(name, function () {
          diag("inject-fired", { slot: name, tag: tag });
          try {
            ctx.slots.register(
              {
                name: name,
                id: "guard-toggle-" + tag,
                order: order,
                label: function () {
                  return "护航";
                },
                inject: function (sessionId) {
                  return { sessionId: sessionId };
                },
              },
              function SlotComponent(props) {
                return createElement(GuardToggle, {
                  sessionId: props && props.sessionId,
                  tag: tag,
                });
              },
            );
            diag("register-ok", { slot: name, tag: tag, order: order });
          } catch (error) {
            diag("register-error", { slot: name, tag: tag,
                                     message: String((error && error.message) || error) });
          }
        });
      } catch (error) {
        diag("inject-error", { slot: name, tag: tag,
                               message: String((error && error.message) || error) });
      }
    }

    function apply(ctx) {
      try {
        installStyles();
      } catch {
        /* 忽略 */
      }
      // 先把 ctx 的形状报到桥上：有没有 slots、slots 上有哪些方法
      diag("apply", {
        hasCtx: Boolean(ctx),
        ctxKeys: (function () {
          try {
            return Object.keys(ctx || {}).slice(0, 40);
          } catch {
            return null;
          }
        })(),
        slotKeys: (function () {
          try {
            return Object.keys((ctx && ctx.slots) || {}).slice(0, 40);
          } catch {
            return null;
          }
        })(),
        sessions: (function () {
          try {
            return Object.keys((ctx && ctx.sessions) || {}).slice(0, 20);
          } catch {
            return null;
          }
        })(),
        href: String(location.href).slice(0, 120),
      });

      var tries = function () {
        for (var i = 0; i < SLOT_CANDIDATES.length; i += 1) {
          trySlot(ctx, SLOT_CANDIDATES[i][0], SLOT_CANDIDATES[i][1], SLOT_CANDIDATES[i][2]);
        }
      };
      // 只注册一次：重复注册会被拒（日志里 "slot ... already ..."），纯噪音
      tries();
    }

    module.exports = {
      apply: apply,
      inject: inject,
      GuardToggle: GuardToggle,
      label: label,
      titleFor: titleFor,
      tone: tone,
      PORT_BASE: PORT_BASE,
      SLOT_CANDIDATES: SLOT_CANDIDATES,
    };

    return module.exports;
  },
});
