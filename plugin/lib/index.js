/**
 * dsh-guard-ui — 宿主入口（装配层）
 *
 * 只做一件事：起回环桥，让客户端按钮能读写守护状态、并在开启时把守护拉起来。
 * 不注册任何工具（不占模型的上下文），不碰 DSH 自己的文件。
 */
import { PACKAGE_ID } from "./core.js";
import { startBridge } from "./bridge.js";

export { PACKAGE_ID } from "./core.js";
export { originAllowed } from "./bridge.js";

export const inject = [];

export function apply(ctx) {
  let log = () => {};
  try {
    const candidate = ctx && ctx.logger;
    const sink = typeof candidate === "function" ? candidate(PACKAGE_ID) : candidate;
    log = (message) => {
      try {
        if (sink && typeof sink.info === "function") sink.info(`[${PACKAGE_ID}] ${message}`);
        else console.info(`[${PACKAGE_ID}] ${message}`);
      } catch {
        /* 日志不许抛 */
      }
    };
  } catch {
    /* 没有 logger 就静默 */
  }

  let server = { close: () => {} };
  let disposed = false;
  startBridge(log)
    .then((s) => {
      server = s;
      if (disposed) server.close();
    })
    .catch((error) => log(`桥接未启动：${String(error)}`));

  try {
    if (ctx && ctx.effect) {
      ctx.effect(() => () => {
        disposed = true;
        try {
          server.close();
        } catch {
          /* 关不掉也不影响退出 */
        }
      });
    }
  } catch {
    /* dispose 挂钩失败可忽略 */
  }
}

export default { apply, inject };
