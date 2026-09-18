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
  if (!Array.isArray(data.seats) || !data.seats.length) errors.push("seats 缺失");
  if (!data.occupants || typeof data.occupants !== "object") errors.push("occupants 缺失");
  if (data.currentHand !== null && (typeof data.currentHand !== "object" || data.currentHand === undefined)) {
    errors.push("currentHand 非法");
  }
  if (!Number.isInteger(data.capacity) || data.capacity < 2 || data.capacity > 9
      || data.seats?.length !== data.capacity) errors.push("capacity/seats 不一致");
  if (!data.blindLevel || !Number.isSafeInteger(data.blindLevel.bb) || data.blindLevel.bb <= 0
      || !Number.isSafeInteger(data.blindLevel.sb) || data.blindLevel.sb <= 0
      || data.blindLevel.sb > data.blindLevel.bb) errors.push("blindLevel 非法");
  if (!data.icm || !Array.isArray(data.icm.payouts) || !Array.isArray(data.recentHands)) errors.push("ICM/历史结构非法");
  if (!data.occupants?.[data.heroOccupantId]) errors.push("我的身份缺失");
  if (!data.positions || !Number.isInteger(data.handNumber)) errors.push("位置/手号缺失");
  const ids = new Set(), seated = new Set();
  for (const seat of Array.isArray(data.seats) ? data.seats : []) {
    if (!seat || !Number.isInteger(seat.id) || seat.id < 1 || seat.id > data.capacity || ids.has(seat.id)) {
      errors.push("座位结构非法"); continue;
    }
    ids.add(seat.id);
    if (seat.occupantId) {
      const occ = data.occupants?.[seat.occupantId];
      if (seated.has(seat.occupantId) || !occ || occ.id !== seat.occupantId
          || !Number.isSafeInteger(occ.confirmedChips) || occ.confirmedChips < 0) errors.push("在座玩家非法");
      seated.add(seat.occupantId);
    }
  }
  const hand = data.currentHand;
  if (data.phase !== "ended" && (!hand || typeof hand.handId !== "string"
      || !Number.isInteger(hand.revision) || !Array.isArray(hand.ops)
      || !Array.isArray(hand.heroCards) || hand.heroCards.length !== 2
      || !Array.isArray(hand.context?.participants) || !hand.context?.blinds)) errors.push("当前手结构非法");
  if (data.phase === "settling" && (!Array.isArray(data.settlementDraft?.rows)
      || data.settlementDraft.sourceHandId !== hand?.handId)) errors.push("结算草稿非法");
  if (data.nextHandDraft && (!Array.isArray(data.nextHandDraft.rosterEdits)
      || !data.nextHandDraft.blindLevel || !Array.isArray(data.nextHandDraft.icm?.payouts))) errors.push("下一手草稿非法");
  // Nested persisted collections are untrusted too; reject before any renderer dereferences them.
  const list = value => Array.isArray(value) ? value : [];
  const object = value => !!value && typeof value === "object" && !Array.isArray(value);
  const chips = value => Number.isSafeInteger(value) && value >= 0;
  const context = value => object(value) && Array.isArray(value.participants)
    && value.participants.length >= 2 && value.participants.every(p => object(p)
      && Number.isInteger(p.seat_id) && typeof p.occupant_id === "string" && chips(p.starting_chips))
    && object(value.blinds) && object(value.icm) && Array.isArray(value.icm.payouts);
  const op = value => object(value) && (value.op === "action"
    ? Number.isInteger(value.seat_id) && ["fold", "call", "check", "raise", "allin"].includes(value.type)
    : value.op === "board" && Array.isArray(value.cards) && value.cards.every(c => typeof c === "string"));
  if (hand && (!context(hand.context) || list(hand.ops).some(value => !op(value)))) errors.push("当前手内容非法");
  if (list(data.recentHands).some(h => !object(h) || typeof h.handId !== "string"
      || !context(h.context) || !object(h.finalChipsByOccupant))) errors.push("历史内容非法");
  if (!object(data.learningJobs) || Object.values(data.learningJobs).some(job => !object(job)
      || !object(job.payload) || !context(job.payload.context) || !Array.isArray(job.payload.ops)
      || job.payload.ops.some(value => !op(value)))) errors.push("学习任务非法");
  if (data.settlementDraft && list(data.settlementDraft.rows).some(row => !object(row)
      || !Number.isInteger(row.seatId) || typeof row.occupantId !== "string"
      || (row.finalChips !== null && !chips(row.finalChips)))) errors.push("结算行非法");
  if (data.nextHandDraft && list(data.nextHandDraft.rosterEdits).some(edit => !object(edit)
      || !["enter", "leave", "move"].includes(edit.type))) errors.push("人员草稿非法");
  return { ok: errors.length === 0, errors };
}

/**
 * 原子保存：先序列化校验，再 setItem。
 * 返回 {ok}；失败时 {ok:false, error}，调用方不得把 UI 标成已保存（L-04）。
 */
export function saveSession(session, expectedRaw) {
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
    if (expectedRaw !== undefined && localStorage.getItem(KEY) !== expectedRaw) {
      return { ok: false, error: "conflict" };
    }
    localStorage.setItem(KEY, text);
    return { ok: true, raw: text };
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
  // 旧版本进入结算后才增加 revision，草稿恰好少 1；只修复这一已知格式。
  if (data.phase === "settling" && data.settlementDraft.sourceRevision === data.currentHand.revision - 1) {
    data.settlementDraft.sourceRevision = data.currentHand.revision;
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
