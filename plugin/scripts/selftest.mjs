// 宿主桥自检：起服务 → GET /state → POST /state → 关掉。
// 用法: node scripts/selftest.mjs
import { startBridge } from "../lib/bridge.js";

const log = (m) => console.log("[bridge]", m);
const server = await startBridge(log);
if (!server.port) {
  console.error("✗ 桥没起来");
  process.exit(1);
}

const base = `http://127.0.0.1:${server.port}`;
let ok = 0;

const got = await fetch(`${base}/state?plugin=dsh-guard-ui`);
const state0 = await got.json();
console.log("GET /state ->", got.status, {
  plugin: state0.plugin, enabled: state0.enabled, running: state0.running,
  guardExists: state0.guardExists,
});
if (got.status === 200 && state0.plugin === "dsh-guard-ui") ok += 1;

const off = await fetch(`${base}/state`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ enabled: false, reason: "selftest" }),
});
const offBody = await off.json();
console.log("POST enabled=false ->", off.status, { enabled: offBody.enabled });
if (off.status === 200 && offBody.enabled === false) ok += 1;

const on = await fetch(`${base}/state`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ enabled: true, reason: "selftest" }),
});
const onBody = await on.json();
console.log("POST enabled=true  ->", on.status, {
  enabled: onBody.enabled, spawned: onBody.spawned,
});
if (on.status === 200 && onBody.enabled === true) ok += 1;

const bad = await fetch(`${base}/state`, {
  method: "POST",
  headers: { "content-type": "text/plain" },
  body: "enabled=true",
});
console.log("POST 非 json ->", bad.status, "(期望 415)");
if (bad.status === 415) ok += 1;

const forbidden = await fetch(`${base}/state`, { headers: { origin: "https://evil.example" } });
console.log("GET 外站 Origin ->", forbidden.status, "(期望 403)");
if (forbidden.status === 403) ok += 1;

const dshApp = await fetch(`${base}/state`, { headers: { origin: "dsh-app://app" } });
console.log("GET dsh-app:// Origin ->", dshApp.status, "(期望 200)");
if (dshApp.status === 200) ok += 1;

server.close();
console.log(ok === 6 ? "✅ 6/6 通过" : `✗ ${ok}/6 通过`);
process.exit(ok === 6 ? 0 : 1);
