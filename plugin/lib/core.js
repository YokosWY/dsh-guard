/**
 * dsh-guard-ui — 常量与状态文件
 *
 * 状态只有两个文件，都在 dsh-guard 项目里，宿主和 Python 守护共用：
 *   state/control.json    {"enabled": true|false, "reason": "...", "by": "ui|cli", "updated_at": "..."}
 *   state/heartbeat.json  {"pid": 1234, "ts": "..."}（守护每 30 秒写一次）
 *
 * 写一律 tmp + rename，失败不抛；坏文件一律只读兜底，不覆写。
 */
import { readFileSync, writeFileSync, renameSync, mkdirSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";

export const PACKAGE_ID = "dsh-guard-ui";
export const PORT = Number(process.env.DSH_GUARD_UI_PORT || 43891);
export const PORT_SPAN = 10; // 43891..43900；两套 DSH 同时开时靠这段区间避让
export const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

export const GUARD_ROOT = process.env.DSH_GUARD_ROOT || "D:\\AI\\Projects\\dsh-guard";
export const STATE_DIR = join(GUARD_ROOT, "state");
export const CONTROL_FILE = join(STATE_DIR, "control.json");
export const HEARTBEAT_FILE = join(STATE_DIR, "heartbeat.json");
export const INCIDENT_FILE = join(GUARD_ROOT, "logs", "incidents.jsonl");
export const GUARD_SCRIPT = join(GUARD_ROOT, "guard.py");
/** 客户端埋点：客户端有没有跑、哪些插槽存在/注入成功，都写这里 */
export const DIAG_FILE = join(STATE_DIR, "client-diag.jsonl");

/** 心跳多久算「还在跑」 */
export const HEARTBEAT_STALE_SECONDS = 90;

/** 进程还在不在：心跳没过期不代表进程活着（实测被杀后 90 秒内都会显示"运行中"）。 */
export function pidAlive(pid) {
  if (typeof pid !== "number" || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return Boolean(error && error.code === "EPERM"); // 存在但没权限 = 活着
  }
}

export function readJson(file) {
  try {
    if (!existsSync(file)) return null;
    const raw = readFileSync(file, "utf8");
    const data = JSON.parse(raw);
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
}

/** 原子写：tmp → fsync 由 Python 侧保证；这里只保证不出现半截文件。 */
export function writeJson(file, data) {
  mkdirSync(dirname(file), { recursive: true });
  const tmp = `${file}.tmp-${process.pid}-${Date.now()}`;
  writeFileSync(tmp, `${JSON.stringify(data, null, 1)}\n`, "utf8");
  renameSync(tmp, file);
}

export function readControl() {
  const data = readJson(CONTROL_FILE);
  return {
    enabled: data && typeof data.enabled === "boolean" ? data.enabled : true,
    reason: (data && typeof data.reason === "string" && data.reason) || "",
    by: (data && typeof data.by === "string" && data.by) || "",
    updated_at: (data && typeof data.updated_at === "string" && data.updated_at) || "",
  };
}

export function writeControl(enabled, reason, by) {
  const next = {
    enabled: Boolean(enabled),
    reason: String(reason || ""),
    by: String(by || "ui"),
    updated_at: new Date().toISOString(),
  };
  writeJson(CONTROL_FILE, next);
  return next;
}

export function readHeartbeat() {
  const data = readJson(HEARTBEAT_FILE);
  if (!data) return { running: false, age: null, pid: null };
  let age = null;
  try {
    age = (Date.now() - statSync(HEARTBEAT_FILE).mtimeMs) / 1000;
  } catch {
    age = null;
  }
  if (typeof data.ts === "string") {
    const t = Date.parse(data.ts);
    if (Number.isFinite(t)) age = (Date.now() - t) / 1000;
  }
  return {
    running: age !== null && age <= HEARTBEAT_STALE_SECONDS,
    age: age === null ? null : Math.round(age),
    pid: typeof data.pid === "number" ? data.pid : null,
  };
}

/** 读事故台账的最后一条（文件可能很大，只读尾部 8KB）。 */
export function readLastIncident() {
  try {
    if (!existsSync(INCIDENT_FILE)) return null;
    const fd = readFileSync(INCIDENT_FILE, "utf8");
    const lines = fd.trim().split("\n");
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const line = lines[i].trim();
      if (!line) continue;
      try {
        return JSON.parse(line);
      } catch {
        continue;
      }
    }
    return null;
  } catch {
    return null;
  }
}

export function guardExists() {
  try {
    return existsSync(GUARD_SCRIPT);
  } catch {
    return false;
  }
}
