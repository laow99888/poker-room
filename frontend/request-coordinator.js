// 请求协调器（契约 S03/S04）：view 与 advice 的请求状态分离，
// 以 {sessionId, handId, handRevision, requestId} 守卫所有异步写回。
// 不自行改变牌桌成员，不做 DOM 渲染——由调用方在守卫通过的回调里写状态。
"use strict";

import { newId } from "./session.js";

function sourceOf(session) {
  return {
    sessionId: session.sessionId,
    handId: session.currentHand ? session.currentHand.handId : null,
    handRevision: session.currentHand ? session.currentHand.revision : 0,
  };
}

function matches(current, captured) {
  return current.sessionId === captured.source.sessionId
    && current.handId === captured.source.handId
    && current.handRevision === captured.source.handRevision;
}

// 实时守卫：捕获值 vs 当前会话实时四元组（S03——所有 await 之后验证）
function stillCurrent(liveSession, captured) {
  return matches(sourceOf(liveSession), captured);
}

export function createCoordinator(session) {
  let viewReq = null;     // {id, source, controller, state}
  let adviceReq = null;
  let live = session;     // 实时会话引用；守卫按其当前 revision 比对（S03）
  let counter = 0;

  function nextId() { counter += 1; return `r${counter}`; }

  return {
    /** 手/会话推进时调用：作废全部在途请求（守卫起效，Abort 仅为优化）。 */
    resync(session2) {
      live = session2;
      for (const req of [viewReq, adviceReq]) {
        if (req && req.controller) { try { req.controller.abort(); } catch (_e) { /* noop */ } }
      }
      viewReq = null;
      adviceReq = null;
    },

    currentSource() { return sourceOf(live); },

    /**
     * 发起 view 请求（动作验证）。同一时刻至多一个：新请求直接取代旧的。
     * hooks: {onAccept(actionOp), onStale(body), onError(err, op), onDone()}
     * 返回本次 requestId。
     */
    submitView(session2, payload, hooks) {
      const id = nextId();
      const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      viewReq = { id, source: sourceOf(session2), controller, state: "pending", payload: null };
      const captured = viewReq;
      (async () => {
        try {
          const res = await fetch("/api/hand/v2/view", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: controller ? controller.signal : undefined,
          });
          const body2 = await res.json();   // 正文读取后再守卫（S-07）
          if (captured !== viewReq || !stillCurrent(live, captured)) {
            if (hooks.onStale) hooks.onStale(body2);
            return;
          }
          captured.state = "done";
          captured.payload = body2;
          viewReq = null;
          if (res.ok) {
            if (hooks.onAccept) hooks.onAccept(body2);
          } else if (hooks.onError) {
            hooks.onError(new Error(body2.detail || `HTTP ${res.status}`));
          }
        } catch (err) {
          if (captured !== viewReq || !matches(currentSource, captured)) return;
          viewReq = null;
          if (hooks.onError) hooks.onError(err);
        } finally {
          if (hooks.onDone) hooks.onDone();
        }
      })();
      return id;
    },

    /**
     * 发起 advice 请求（局面推演）。失败只影响推演区（S04.4）。
     * hooks: {onResult(body), onStale(body), onError(err), onDone()}
     */
    submitAdvice(session2, payload, hooks) {
      const id = nextId();
      const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      adviceReq = { id, source: sourceOf(session2), controller, state: "pending" };
      const captured = adviceReq;
      (async () => {
        try {
          const res = await fetch("/api/hand/v2/advice", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: controller ? controller.signal : undefined,
          });
          const body2 = await res.json();
          if (captured !== adviceReq || !stillCurrent(live, captured)) {
            if (hooks.onStale) hooks.onStale(body2);
            return;
          }
          adviceReq = null;
          if (res.ok) {
            if (hooks.onResult) hooks.onResult(body2);
          } else if (hooks.onError) {
            hooks.onError(new Error(body2.detail || `HTTP ${res.status}`));
          }
        } catch (err) {
          if (captured !== adviceReq || !matches(currentSource, captured)) return;
          adviceReq = null;
          if (hooks.onError) hooks.onError(err);
        } finally {
          if (hooks.onDone) hooks.onDone();
        }
      })();
      return id;
    },

    /** 用户继续录入/撤销时立刻失效旧推演（S04.3），不等新 view。 */
    invalidateAdvice() {
      if (adviceReq && adviceReq.controller) {
        try { adviceReq.controller.abort(); } catch (_e) { /* noop */ }
      }
      adviceReq = null;
    },

    /** S04.1：view pending 时禁用依赖下一行动者的按钮。 */
    viewPending() { return !!viewReq; },
    advicePending() { return !!adviceReq; },
  };
}
