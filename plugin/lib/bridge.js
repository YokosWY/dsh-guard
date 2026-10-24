/**
 * dsh-guard-ui — 回环桥
 *
 * 只绑 127.0.0.1，端口 43891–43900 退让；带 Origin 的请求只放行本机页面与 dsh-app://。
 *   GET  /state?plugin=dsh-guard-ui   → {plugin, enabled, running, age, pid, guardExists, last, ports}
 *   POST /state  {"enabled": true}    → 写 control.json；开启时若守护没在跑就拉起它
 *
 * 安全：POST 必须是 application/json，Origin 白名单；不提供任何路径参数（写死项目目录）。
 */
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync, statSync, renameSync } from "node:fs";
import { dirname } from "node:path";
import { PORT, PORT_SPAN, LOCAL_HOSTS, PACKAGE_ID, GUARD_SCRIPT, GUARD_ROOT,
         DIAG_FILE,
         guardExists, pidAlive, readControl, writeControl, readHeartbeat,
         readLastIncident } from "./core.js";

/** 客户端埋点：滚动到 256KB 就换一个文件，别让它长成第二个日志系统。 */
function appendDiag(record) {
  try {
    mkdirSync(dirname(DIAG_FILE), { recursive: true });
    try {
      if (statSync(DIAG_FILE).size > 256 * 1024) {
        renameSync(DIAG_FILE, `${DIAG_FILE}.1`);
      }
    } catch {
      /* 文件不存在就算了 */
    }
    appendFileSync(DIAG_FILE, `${JSON.stringify(record)}\n`, "utf8");
  } catch {
    /* 埋点失败绝不能影响插件 */
  }
}

export function originAllowed(origin) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.protocol === "dsh-app:") return true;
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    return LOCAL_HOSTS.has(url.hostname);
  } catch {
    return false;
  }
}

const PYTHONW = "D:\\Softstore\\Python\\pythonw.exe";

function spawnGuard(log) {
  try {
    if (!guardExists()) {
      log("guard.py 不存在，跳过启动");
      return { started: false, why: "guard.py missing" };
    }
    // 关键：不能直接 spawn pythonw —— 那样守护是 DSH 的子孙进程，DSH 一退出它就跟着死
    // （2026-10-24 实测：DSH 重启时 pid 32760 被一起带走，日志里没有任何退出记录）。
    // 走 `cmd /c start` 让 cmd 当爹、cmd 立刻退出，守护就被挂到系统上，DSH 崩了它还在。
    const child = spawn("cmd.exe", ["/c", "start", "", PYTHONW, GUARD_SCRIPT], {
      cwd: GUARD_ROOT,
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    });
    child.unref();
    log(`已通过 cmd/start 拉起守护（cmd pid=${child.pid}）`);
    return { started: true, pid: child.pid };
  } catch (error) {
    log(`拉起守护失败：${String(error)}`);
    return { started: false, why: String(error) };
  }
}

function snapshotState() {
  const control = readControl();
  const beat = readHeartbeat();
  const last = readLastIncident();
  // 心跳新鲜 ≠ 进程活着：被杀之后 90 秒内心跳还"新鲜"，这里再核一次 pid
  const alive = beat.running && pidAlive(beat.pid);
  return {
    plugin: "dsh-guard-ui",
    packageId: PACKAGE_ID,
    enabled: control.enabled,
    reason: control.reason,
    by: control.by,
    updated_at: control.updated_at,
    running: alive,
    heartbeat_age: beat.age,
    pid: beat.pid,
    guardExists: guardExists(),
    guardRoot: GUARD_ROOT,
    last: last
      ? { t: last.t || null, kind: last.kind || null, detail: String(last.detail || "").slice(0, 200) }
      : null,
  };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 4096) {
        reject(new Error("body too large"));
        req.destroy();
      }
    });
    req.on("end", () => resolve(raw));
    req.on("error", reject);
  });
}

export function startBridge(log) {
  let lastClientSeen = 0;
  const server = createServer(async (req, res) => {
    const origin = req.headers.origin;
    const allowed = originAllowed(origin);
    const headers = {
      ...(allowed && origin ? { "Access-Control-Allow-Origin": origin, Vary: "Origin" } : {}),
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "content-type",
      "Access-Control-Max-Age": "600",
      "Cache-Control": "no-store",
      "content-type": "application/json; charset=utf-8",
    };

    if (!allowed) {
      res.writeHead(403, headers);
      res.end(JSON.stringify({ error: "origin not allowed" }));
      return;
    }
    if (req.method === "OPTIONS") {
      res.writeHead(204, headers);
      res.end();
      return;
    }

    let url;
    try {
      url = new URL(req.url, "http://127.0.0.1");
    } catch {
      res.writeHead(400, headers);
      res.end(JSON.stringify({ error: "bad url" }));
      return;
    }

    if (url.pathname === "/state" && req.method === "GET") {
      // 客户端一旦跑起来就会来探活；这里留个心跳痕迹，用来区分「客户端没跑」和「跑了但没显示」
      if (url.searchParams.get("plugin")) {
        const now = Date.now();
        if (now - lastClientSeen > 60000) {
          lastClientSeen = now;
          appendDiag({ t: new Date().toISOString(), event: "client-poll",
                       origin: origin || null, ua: String(req.headers["user-agent"] || "").slice(0, 80) });
        }
      }
      res.writeHead(200, headers);
      res.end(JSON.stringify(snapshotState()));
      return;
    }

    // 客户端埋点：不论成败都收，写 state/client-diag.jsonl
    if (url.pathname === "/diag" && req.method === "POST") {
      try {
        const raw = await readBody(req);
        let body = {};
        try {
          body = JSON.parse(raw || "{}");
        } catch {
          body = { event: "bad-json", raw: String(raw).slice(0, 200) };
        }
        appendDiag({ t: new Date().toISOString(), ...body });
      } catch (error) {
        appendDiag({ t: new Date().toISOString(), event: "diag-post-error", error: String(error) });
      }
      res.writeHead(204, headers);
      res.end();
      return;
    }

    if (url.pathname === "/state" && req.method === "POST") {
      const ctype = String(req.headers["content-type"] || "");
      if (!ctype.includes("application/json")) {
        res.writeHead(415, headers);
        res.end(JSON.stringify({ error: "expect application/json" }));
        return;
      }
      try {
        const raw = await readBody(req);
        const body = JSON.parse(raw || "{}");
        if (typeof body.enabled !== "boolean") {
          res.writeHead(400, headers);
          res.end(JSON.stringify({ error: "enabled must be boolean" }));
          return;
        }
        const reason = typeof body.reason === "string" ? body.reason.slice(0, 200) : "";
        writeControl(body.enabled, reason, "ui");
        let spawned = { started: false };
        const beat = readHeartbeat();
        const already = beat.running && pidAlive(beat.pid);
        if (body.enabled && !already) {
          spawned = spawnGuard(log);
          // 拉起来要给进程一点时间写心跳，否则界面会先闪一下"待启"
          if (spawned.started) {
            for (let i = 0; i < 20; i += 1) {
              await new Promise((r) => setTimeout(r, 100));
              if (readHeartbeat().running) break;
            }
          }
        }
        res.writeHead(200, headers);
        res.end(JSON.stringify({ ...snapshotState(), spawned }));
      } catch (error) {
        res.writeHead(500, headers);
        res.end(JSON.stringify({ error: String(error && error.message ? error.message : error) }));
      }
      return;
    }

    res.writeHead(404, headers);
    res.end(JSON.stringify({ error: "not found" }));
  });

  return new Promise((resolve) => {
    let port = PORT;
    let attempts = 0;
    const tryListen = () => {
      server.once("error", (error) => {
        attempts += 1;
        if (attempts < PORT_SPAN && error && error.code === "EADDRINUSE") {
          port = PORT + attempts;
          tryListen();
          return;
        }
        log(`桥接端口都占用了：${String(error)}`);
        resolve({ port: null, close: () => {} });
      });
      server.listen(port, "127.0.0.1", () => {
        log(`桥接已启动 127.0.0.1:${port}`);
        resolve({
          port,
          close: () => {
            try {
              server.close();
            } catch {
              /* 忽略 */
            }
          },
        });
      });
    };
    tryListen();
  });
}
