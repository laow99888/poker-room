// 本地持久化适配（契约 L）：schema 校验、保存/恢复、写失败报告。
// 不计算筹码输赢——领域迁移全部在 session.js。
"use strict";

const KEY = "paishi_tournament_session_v1";

function hasStorage() {
  try {
    return typeof localStorage !== "undefined" && !!localStorage;
  } catch (_e) {
    return false;   // 隐私模式等场景：localStorage 访问直接抛异常
  }
}

/** 保存前校验最小 schema；返回 {ok, errors}。 */
export function validateSchema(data) {
  const errors = [];
  if (!data || typeof data !== "object") return { ok: false, errors: ["非对象"] };
  if (data.schemaVersion !== 1) errors.push("schemaVersion 必须为 1");
  if (typeof data.sessionId !== "string" || !data.sessionId) errors.push("sessionId 缺失");
  if (!Number.isInteger(data.sessionRevision) || data.sessionRevision < 1) {
    errors.push("sessionRevision 非法");
  }
  if (typeof data.phase !== "string"
    || !["setup", "ready", "playing", "settling", "ended"].includes(data.phase)) {
    errors.push("phase 非法");
  }
  if (!Number.isArray(data.seats) || !data.seats.length) errors.push("seats 缺失");
  if (!data.occupants || typeof data.occupants !== "object") errors.push("occupants 缺失");
  if (data.currentHand !== null && (typeof data.currentHand !== "object" || data.currentHand === undefined)) {
    errors.push("currentHand 非法");
  }
  return { ok: errors.length === 0, errors };
}

/**
 * 原子保存：先序列化校验，再 setItem。
 * 返回 {ok}；失败时 {ok:false, error}，调用方不得把 UI 标成已保存（L-04）。
 */
export function saveSession(session) {
  const check = validateSchema(session);
  if (!check.ok) return { ok: false, error: "schema:" + check.errors.join(";") };
  let text;
  try {
    text = JSON.stringify(session);
  } catch (e) {
    return { ok: false, error: "serialize:" + e.message };
  }
  if (!hasStorage()) return { ok: false, error: "no_storage" };
  try {
    localStorage.setItem(KEY, text);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: "quota:" + e.message };   // 额度不足等
  }
}

/** 恢复：损坏或未来 schema 时返回 {status:"corrupt"|"future", raw}，不覆盖原文本。 */
export function loadSession() {
  if (!hasStorage()) return { status: "empty" };
  let raw;
  try {
    raw = localStorage.getItem(KEY);
  } catch (e) {
    return { status: "empty" };
  }
  if (!raw) return { status: "empty" };
  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    return { status: "corrupt", raw, error: String(e) };
  }
  const check = validateSchema(data);
  if (!check.ok) {
    if (data && typeof data.schemaVersion === "number" && data.schemaVersion > 1) {
      return { status: "future", raw, errors: check.errors };
    }
    return { status: "corrupt", raw, errors: check.errors };
  }
  return { status: "ok", session: data, raw };
}

export function clearSession() {
  if (!hasStorage()) return;
  try { localStorage.removeItem(KEY); } catch (_e) { /* 忽略 */ }
}

export function exportRaw() {
  if (!hasStorage()) return null;
  try { return localStorage.getItem(KEY); } catch (_e) { return null; }
}

/** 旧 paishi_config_v2 仅作新建桌预填（L-06）；不推断英雄座位。 */
export function loadLegacyConfig() {
  if (!hasStorage()) return null;
  try {
    const raw = localStorage.getItem("paishi_config_v2");
    if (!raw) return null;
    const cfg = JSON.parse(raw);
    if (!cfg || typeof cfg !== "object") return null;
    return {
      capacity: [6, 8, 9].includes(cfg.player_count) ? cfg.player_count : 6,
      sb: Number(cfg.sb) || 100,
      bb: Number(cfg.bb) || 200,
      anteEach: Number(cfg.ante) || 0,
      defaultChips: Number(cfg.stack) || Number(cfg.bb) * 50 || 10000,
      payouts: Array.isArray(cfg.payouts) ? cfg.payouts : [],
    };
  } catch (_e) {
    return null;
  }
}

/** L-08：多 tab 冲突——别的 tab 写过本会话则提示载入最新。 */
export function onStorageChange(handler) {
  if (!hasStorage() || typeof window === "undefined") return () => {};
  const fn = (e) => {
    if (e.key === KEY) handler(e.newValue);
  };
  window.addEventListener("storage", fn);
  return () => window.removeEventListener("storage", fn);
}
