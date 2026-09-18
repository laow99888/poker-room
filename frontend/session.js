// 锦标赛连续牌桌领域模型（纯函数）：状态迁移、校验、座位映射输入、BB 换算、
// 结算草稿。不访问 DOM / fetch / localStorage。契约见
// docs/plans/tournament-session/contracts.md（M=身份、C=金额、P=位置、S=状态机）。
"use strict";

/* ---------------------------------------------------------------- 工具 */

export function newId(prefix) {
  const raw = (globalThis.crypto && globalThis.crypto.randomUUID)
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${raw}`;
}

export class DomainError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

const MAX_SAFE = Number.MAX_SAFE_INTEGER;

function assertIntChips(value, label) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new DomainError("bad_amount", `${label}必须为非负安全整数，收到 ${value}`);
  }
}

function clone(value) {
  return structuredClone(value);
}

/* ------------------------------------------------ C01：BB 换算（十进制精确） */

// 解析十进制字符串为 {scaled:BigInt, scale:BigInt}；非法返回 null。
function parseDecimal(text) {
  const m = /^(\d*)(?:\.(\d*))?$/.exec(String(text).trim());
  if (!m || (m[1] === "" && (m[2] === undefined || m[2] === ""))) return null;
  const intPart = m[1] || "0";
  const frac = m[2] || "";
  return { scaled: BigInt(intPart + frac), scale: BigInt(frac.length) };
}

// BB 字符串 × 大盲 → 换算值/取整值（最近整数，恰好 .5 向上，十进制精确）。
export function chipsFromBB(bbText, bb) {
  const parsed = parseDecimal(bbText);
  const bbInt = Number(bb);
  if (!parsed || !Number.isSafeInteger(bbInt) || bbInt <= 0) {
    return { ok: false, error: "invalid_input" };
  }
  const { scaled, scale } = parsed;
  // chips_exact = scaled * bb / 10^scale；四舍五入（.5 向上）：
  // floor((scaled*bb*2 + 10^scale) / (2*10^scale))
  const denom = 10n ** scale;
  const rounded = (scaled * BigInt(bbInt) * 2n + denom) / (denom * 2n);
  const exactNum = scaled * BigInt(bbInt);
  const exactInt = exactNum % denom === 0n;
  const exactText = exactInt
    ? (exactNum / denom).toString()
    : `${exactNum / denom}.${(exactNum % denom).toString().padStart(Number(scale), "0").replace(/0+$/, "")}`;
  const chips = Number(rounded);
  if (!Number.isSafeInteger(chips) || chips < 0) {
    return { ok: false, error: "out_of_range" };
  }
  return {
    ok: true,
    chips,
    exactText,                       // 十进制精确换算值文本
    rounded: chips,                  // 取整值
    changed: !exactInt || exactNum / denom !== rounded,
  };
}

// 筹码 → BB 显示（1 位小数，仅展示，不回写）。
export function chipsToBBText(chips, bb) {
  if (!Number.isSafeInteger(chips) || !Number.isSafeInteger(bb) || bb <= 0) return "—";
  const whole = Math.floor(chips / bb);
  const frac = Math.round(((chips % bb) / bb) * 10);
  return frac >= 10 ? `${whole + 1}.0` : `${whole}.${frac}`;
}

export function isSafeAmount(n) { return Number.isSafeInteger(n); }

/* ------------------------------------------------------------ M01/M02 身份 */

export function heroSeatOf(session) {
  if (!session.currentHand) {
    for (const seat of session.seats) {
      if (session.occupants[seat.occupantId]?.status === "active"
        && seat.occupantId === session.heroOccupantId) return seat.id;
    }
    return null;
  }
  for (const p of session.currentHand.context.participants) {
    if (p.occupant_id === session.heroOccupantId) return p.seat_id;
  }
  return null;
}

export function activeOccupantAt(session, seatId) {
  const seat = session.seats.find((s) => s.id === seatId);
  if (!seat || !seat.occupantId) return null;
  const occ = session.occupants[seat.occupantId];
  return occ && occ.status === "active" ? occ : null;
}

export function activeSeats(session) {
  return session.seats
    .filter((s) => s.occupantId && session.occupants[s.occupantId]?.status === "active")
    .map((s) => s.id)
    .sort((a, b) => a - b);
}

/* --------------------------------------------------- HandContext（后端契约） */

// 由会话推导当前手上下文（snake_case，作为 prepare/view/advice 的 context）。
// participants 顺序不定义引擎顺序；后端按物理座位/按钮排序。
export function buildHandContext(session, handNumber) {
  const participants = [];
  for (const seat of session.seats) {
    if (!seat.occupantId) continue;
    const occ = session.occupants[seat.occupantId];
    if (!occ || occ.status !== "active") continue;
    participants.push({
      seat_id: seat.id,
      occupant_id: occ.id,
      starting_chips: occ.confirmedChips,
    });
  }
  const profileNames = {};
  if (session.learningEnabled) {
    for (const seat of session.seats) {
      if (!seat.occupantId) continue;
      const occ = session.occupants[seat.occupantId];
      if (occ?.status === "active" && occ.profileName) {
        profileNames[occ.id] = occ.profileName;
      }
    }
  }
  return {
    session_id: session.sessionId,
    capacity: session.capacity,
    hero_occupant_id: session.heroOccupantId,
    participants,
    button_seat_id: session.positions.buttonSeatId,
    small_blind_seat_id: session.positions.smallBlindSeatId,
    big_blind_seat_id: session.positions.bigBlindSeatId,
    blinds: {
      sb: session.blindLevel.sb,
      bb: session.blindLevel.bb,
      ante_each: session.blindLevel.anteEach,
    },
    learning_enabled: session.learningEnabled,
    profile_names: profileNames,
    icm: { scope: session.icm.scope, payouts: session.icm.payouts.slice() },
  };
}

/* -------------------------------------------------------------- S01 建桌 */

// input: {capacity, entries:[{seatId, name?, chips}], heroSeatId,
//         buttonSeatId, sbSeatId, bbSeatId, blindLevel:{sb,bb,anteEach},
//         displayUnit, learningEnabled, icm:{scope,payouts,rosterConfirmed}}
export function createSession(input) {
  const capacity = input.capacity;
  if (!Number.isInteger(capacity) || capacity < 2 || capacity > 9) {
    throw new DomainError("bad_capacity", "座位容量须为 2–9");
  }
  const entries = input.entries;
  if (!Array.isArray(entries) || entries.length < 2 || entries.length > capacity) {
    throw new DomainError("bad_roster", "入局人数须为 2–容量");
  }
  const seats = Array.from({ length: capacity }, (_, i) => ({ id: i + 1, occupantId: null }));
  const occupants = {};
  const seenOcc = new Set();
  for (const e of entries) {
    if (!(e.seatId >= 1 && e.seatId <= capacity) || seats[e.seatId - 1].occupantId) {
      throw new DomainError("bad_seat", `座位非法或重复：${e.seatId}`);
    }
    assertIntChips(e.chips, "起始筹码");
    if (e.chips <= 0) throw new DomainError("bad_amount", "入局起始筹码必须大于 0");
    const id = e.occupantId || newId("p");
    if (seenOcc.has(id)) throw new DomainError("bad_occupant", `occupantId 重复：${id}`);
    seenOcc.add(id);
    seats[e.seatId - 1].occupantId = id;
    occupants[id] = {
      id, status: "active", confirmedChips: e.chips,
      profileName: e.name ? String(e.name) : null,
    };
  }
  const heroOcc = seats[input.heroSeatId - 1]?.occupantId;
  if (!heroOcc) throw new DomainError("bad_hero", "英雄座位必须有人入座");
  const bl = input.blindLevel;
  if (!Number.isSafeInteger(bl.sb) || bl.sb <= 0
    || !Number.isSafeInteger(bl.bb) || bl.bb < bl.sb
    || !Number.isSafeInteger(bl.anteEach) || bl.anteEach < 0) {
    throw new DomainError("bad_blinds", "盲注须为正整数且 bb≥sb，前注 ≥0");
  }
  const positions = normalizePositions({
    buttonSeatId: input.buttonSeatId,
    smallBlindSeatId: input.sbSeatId,
    bigBlindSeatId: input.bbSeatId,
  }, seats, occupants, bl.bb);
  const session = {
    schemaVersion: 1,
    sessionId: newId("s"),
    sessionRevision: 1,
    phase: "ready",
    capacity,
    seats,
    occupants,
    heroOccupantId: heroOcc,
    blindLevel: { sb: bl.sb, bb: bl.bb, anteEach: bl.anteEach },
    displayUnit: input.displayUnit === "chips" ? "chips" : "bb",
    learningEnabled: !!input.learningEnabled,
    icm: normalizeIcm(input.icm, occupants),
    positions,
    previousBigBlindOccupantId: null,
    previousBigBlindSeatId: null,
    handNumber: 1,
    currentHand: null,
    settlementDraft: null,
    nextHandDraft: null,
    lastCommit: null,
    recentHands: [],
    learningJobs: {},
  };
  session.currentHand = makeHandSnapshot(session, 1);
  bumpRevision(session);
  return session;
}

function normalizeIcm(icm, occupants) {
  const scope = icm?.scope === "final_table" ? "final_table" : "off";
  const payouts = Array.isArray(icm?.payouts)
    ? icm.payouts.map((x) => Number(x)) : [];
  if (scope === "final_table") {
    const active = Object.values(occupants).filter((o) => o.status === "active").length;
    if (!payouts.length || payouts.length > active
      || payouts.some((p) => !Number.isFinite(p) || p < 0)) {
      throw new DomainError("bad_icm", "奖金结构无效：需非负有限数值且名次数不超过入局人数");
    }
  }
  return { scope, payouts, rosterConfirmed: scope !== "final_table" ? false : !!icm?.rosterConfirmed };
}

// P02 确认前验证：沿物理顺序盲位/行动顺序自洽。
export function normalizePositions(pos, seats, occupants, _bb) {
  const seatedIds = new Set(seats
    .filter((s) => s.occupantId && occupants[s.occupantId]?.status === "active")
    .map((s) => s.id));
  if (seatedIds.size >= 2) {
    const btn = pos.buttonSeatId;
    if (!Number.isInteger(btn) || !(btn >= 1 && btn <= seats.length)) {
      throw new DomainError("bad_button", "庄位座位非法");
    }
    const bb = pos.bigBlindSeatId;
    if (!seatedIds.has(bb)) throw new DomainError("bad_bb", "大盲位必须为实际入局者");
    const sb = pos.smallBlindSeatId;
    if (sb !== null && sb !== undefined && !seatedIds.has(sb) && sb !== btn) {
      // sb === btn 允许（单挑 BTN 即 SB）；空小盲 sb=null 允许（≥3 人）
      throw new DomainError("bad_sb", "小盲位必须为实际入局者或显式为空");
    }
    // 顺序自洽：SB（若有）是 BTN 后首位入局者、BB 为其下一位；无 SB 时 BB 是 BTN 后首位
    const order = orderFromButton(seats, seatedIds, btn);
    if (seatedIds.size === 2) {
      if (!(sb === btn || sb === null)) {
        if (sb !== order.find((s) => s !== btn)) {
          throw new DomainError("bad_positions", "单挑位置矛盾（BTN 即 SB，另一人为 BB）");
        }
      }
      if (bb === btn || !seatedIds.has(bb)) {
        throw new DomainError("bad_positions", "单挑 BB 不能是 BTN 座位");
      }
    } else if (sb === null || sb === undefined) {
      if (order[0] !== bb) throw new DomainError("bad_positions", "空小盲时大盲必须是庄后首位入局者");
    } else {
      if (order[0] !== sb || order[1] !== bb) {
        throw new DomainError("bad_positions", "盲位与物理顺序矛盾（SB 应为庄后首位，BB 次位）");
      }
    }
  }
  return {
    buttonSeatId: pos.buttonSeatId,
    smallBlindSeatId: sbOf(pos),
    bigBlindSeatId: pos.bigBlindSeatId,
    source: pos.source || "manual",
    confirmed: pos.confirmed ?? false,
  };
}

function sbOf(pos) {
  return pos.smallBlindSeatId === undefined ? null : pos.smallBlindSeatId;
}

// 从按钮起顺时针的入局座位序列（按钮有人时它最后入列——由调用方按需使用）。
function orderFromButton(seats, seatedIds, buttonSeatId) {
  const n = seats.length;
  const out = [];
  for (let step = 1; step <= n; step++) {
    const sid = ((buttonSeatId - 1 + step) % n) + 1;
    if (seatedIds.has(sid)) out.push(sid);
  }
  return out;
}

export function clockwiseOrder(session, fromSeatId) {
  const seated = new Set(activeSeats(session));
  const n = session.capacity;
  const out = [];
  for (let step = 0; step < n; step++) {
    const sid = ((fromSeatId - 1 + step) % n) + 1;
    if (seated.has(sid)) out.push(sid);
  }
  return out;
}

/* --------------------------------------------- P04 显示位置（范围估算标签） */

const POS_TABLES = {
  2: ["BB", "BTN"],
  3: ["SB", "BB", "BTN"],
  4: ["SB", "BB", "CO", "BTN"],
  5: ["SB", "BB", "UTG", "CO", "BTN"],
  6: ["SB", "BB", "UTG", "HJ", "CO", "BTN"],
  7: ["SB", "BB", "UTG", "MP", "HJ", "CO", "BTN"],
  8: ["SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO", "BTN"],
  9: ["SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN"],
};

// range_position 是相对行动顺序的估算标签，不决定实际扣盲（契约 P04）。
export function rangePositionFor(session, seatId) {
  const seated = activeSeats(session);
  const n = seated.length;
  const { buttonSeatId, smallBlindSeatId, bigBlindSeatId } = session.positions;
  if (!seated.includes(seatId)) return null;
  if (smallBlindSeatId == null) {
    // 无小盲：引擎顺序从 BB（庄后首位）开始；(n+1) 人表去 SB；满桌无空位不支持
    if (n + 1 > 9 || seated.length === session.capacity) return null;
    const labels = POS_TABLES[n + 1].filter((r) => r !== "SB");
    const order = clockwiseOrder(session, bigBlindSeatId);
    const idx = order.indexOf(seatId);
    return idx < 0 ? null : labels[idx];
  }
  if (n === 2) return seatId === bigBlindSeatId ? "BB" : "BTN";
  const labels = POS_TABLES[n];
  if (!labels) return null;
  const order = clockwiseOrder(session, smallBlindSeatId);
  const idx = order.indexOf(seatId);
  return idx < 0 ? null : labels[idx];
}

/* ------------------------------------------------------- M03 本手冻结快照 */

function makeHandSnapshot(session, handNumber) {
  return {
    handId: newId("h"),
    handNumber,
    context: buildHandContext(session, handNumber),
    heroCards: [null, null],
    ops: [],
    revision: 1,
    recordQuality: "complete",
    manualCloseReason: null,
  };
}

function bumpRevision(session) {
  session.sessionRevision += 1;
  if (session.currentHand) session.currentHand.revision += 1;
  return session;
}

export function setHeroCards(session, cards) {
  requirePhase(session, ["ready", "playing"]);
  const hand = session.currentHand;
  hand.heroCards = [cards[0] ?? null, cards[1] ?? null];
  return bumpRevision(session);
}

export function pushOp(session, op) {
  requirePhase(session, ["playing"]);
  session.currentHand.ops.push(clone(op));
  return bumpRevision(session);
}

export function undoLastOp(session) {
  requirePhase(session, ["playing"]);
  if (!session.currentHand.ops.length) {
    throw new DomainError("nothing_to_undo", "没有可撤销的操作");
  }
  session.currentHand.ops.pop();
  return bumpRevision(session);
}

// S01：重录本手——保留 context/handId/手数，清动作与底牌；庄位不动。
export function replayHand(session) {
  requirePhase(session, ["ready", "playing", "settling"]);
  const hand = session.currentHand;
  hand.ops = [];
  hand.heroCards = [null, null];
  hand.recordQuality = "complete";
  hand.manualCloseReason = null;
  session.settlementDraft = null;
  session.phase = "ready";
  return bumpRevision(session);
}

// S01：手工结束录入（不完整牌谱）。
export function manualCloseHand(session, reason) {
  requirePhase(session, ["ready", "playing"]);
  const text = String(reason ?? "").trim();
  if (!text) throw new DomainError("reason_required", "手工结束必须说明原因");
  session.currentHand.recordQuality = "manual_close";
  session.currentHand.manualCloseReason = text;
  session.phase = "settling";
  session.settlementDraft = buildSettlementDraft(session, []);
  return bumpRevision(session);
}

export function enterSettling(session, settlementPreview) {
  requirePhase(session, ["playing"]);
  session.phase = "settling";
  session.settlementDraft = buildSettlementDraft(session, settlementPreview || []);
  return bumpRevision(session);
}

export function cancelSettlement(session) {
  requirePhase(session, ["settling"]);
  session.settlementDraft = null;
  session.phase = session.currentHand.ops.length ? "playing" : "ready";
  return bumpRevision(session);
}

export function endSession(session) {
  session.phase = "ended";
  session.settlementDraft = null;
  return bumpRevision(session);
}

/* -------------------------------------------------------- C03/C04 结算草稿 */

// previewRows: [{seat_id, suggested_chips|null, source}]（来自后端 settlement_preview）
export function buildSettlementDraft(session, previewRows) {
  const hand = session.currentHand;
  const bySeat = new Map((previewRows || []).map((r) => [r.seat_id, r]));
  const rows = [];
  for (const p of hand.context.participants) {
    const pre = bySeat.get(p.seat_id);
    const source = pre?.source === "verified" ? "engine_verified" : "pending";
    rows.push({
      seatId: p.seat_id,
      occupantId: p.occupant_id,
      finalChips: pre && pre.source === "verified" ? pre.suggested_chips : null,
      source,
      confirmed: false,
    });
  }
  return {
    sourceHandId: hand.handId,
    sourceRevision: hand.revision,
    rows,
    adjustmentReason: null,
    differenceAccepted: false,
  };
}

export function editSettlementRow(draft, seatId, finalChips, source = "manual") {
  const row = draft.rows.find((r) => r.seatId === seatId);
  if (!row) throw new DomainError("bad_seat", `结算表无此座位：${seatId}`);
  if (finalChips === null || finalChips === "") {
    row.finalChips = null;
    row.source = "pending";
    row.confirmed = false;
    return draft;
  }
  const n = Number(finalChips);
  if (!Number.isSafeInteger(n) || n < 0) {
    throw new DomainError("bad_amount", "余额必须为非负安全整数");
  }
  row.finalChips = n;
  row.source = source === "engine_verified" ? "engine_verified" : "manual";
  return draft;
}

export function confirmAllCurrentValues(draft) {
  for (const row of draft.rows) {
    if (row.finalChips !== null) row.confirmed = true;
  }
  return draft;
}

// C04：校验结算草稿。返回 {ok, delta, errors[]}。
export function validateSettlement(session, draft) {
  const errors = [];
  const hand = session.currentHand;
  if (draft.sourceHandId !== hand.handId) {
    errors.push("结算草稿与本手不匹配");
  }
  const participants = hand.context.participants;
  if (draft.rows.length !== participants.length) errors.push("结算行数与本手入局者不一致");
  const seatSet = new Set(draft.rows.map((r) => r.seatId));
  if (seatSet.size !== draft.rows.length) errors.push("结算行座位重复");
  for (const p of participants) {
    if (!seatSet.has(p.seat_id)) errors.push(`座位 ${p.seat_id} 缺少结算行`);
  }
  let sum = 0;
  for (const row of draft.rows) {
    if (row.finalChips === null || !Number.isSafeInteger(row.finalChips) || row.finalChips < 0) {
      errors.push(`座位 ${row.seatId} 余额待核对`);
      continue;
    }
    sum += row.finalChips;
  }
  const startingSum = participants.reduce((a, p) => a + p.starting_chips, 0);
  if (sum > MAX_SAFE) errors.push("全桌合计超出安全整数");
  const delta = sum - startingSum;
  if (delta !== 0) {
    if (!draft.adjustmentReason || !String(draft.adjustmentReason).trim()) {
      errors.push(`差额 ${delta > 0 ? "+" : ""}${delta} 需要填写原因`);
    }
    if (!draft.differenceAccepted) errors.push(`差额 ${delta > 0 ? "+" : ""}${delta} 需要显式确认`);
  }
  return { ok: errors.length === 0, delta, errors, startingSum, finalSum: sum };
}

/* ---------------------------------------------------- M04 成员操作（手间） */

export function emptyNextHandDraft(session) {
  return {
    rosterEdits: [],
    blindLevel: { ...session.blindLevel },
    positions: null,          // null = 尚未生成/确认；普通轮转提交时自动计算
    learningEnabled: session.learningEnabled,
    icm: clone(session.icm),
  };
}

// edit: {type:'enter', seatId, chips, name?} | {type:'leave', occupantId, reason}
//      | {type:'move', occupantId, targetSeatId}
export function applyRosterEdit(session, draft, edit) {
  requirePhase(session, ["settling"]);
  if (edit.type === "enter") {
    const seat = session.seats.find((x) => x.id === edit.seatId);
    if (!seat) throw new DomainError("bad_seat", "座位不存在");
    const pendingLeave = draft.rosterEdits.some(
      (e) => e.type === "leave"
        && session.seats.find((x) => x.occupantId === e.occupantId)?.id === edit.seatId);
    if (seat.occupantId && !pendingLeave
      && session.occupants[seat.occupantId]?.status === "active") {
      throw new DomainError("seat_occupied", "只能入座空座");
    }
    assertIntChips(edit.chips, "新入座筹码");
    if (edit.chips <= 0) throw new DomainError("bad_amount", "新入座筹码必须大于 0");
    draft.rosterEdits.push({
      type: "enter", seatId: edit.seatId,
      occupantId: newId("p"), chips: edit.chips,
      name: edit.name ? String(edit.name) : null,
    });
  } else if (edit.type === "leave") {
    const occ = session.occupants[edit.occupantId];
    if (!occ || occ.status !== "active") throw new DomainError("bad_occupant", "该玩家不在座");
    if (edit.occupantId === session.heroOccupantId && edit.reason !== "table_transfer") {
      throw new DomainError("bad_leave", "英雄离开本桌将结束会话，请使用结束牌桌");
    }
    draft.rosterEdits.push({ type: "leave", occupantId: edit.occupantId, reason: edit.reason });
  } else if (edit.type === "move") {
    const occ = session.occupants[edit.occupantId];
    if (!occ || occ.status !== "active") throw new DomainError("bad_occupant", "该玩家不在座");
    const target = session.seats.find((x) => x.id === edit.targetSeatId);
    const pendingLeave = draft.rosterEdits.some(
      (e) => e.type === "leave"
        && session.seats.find((x) => x.occupantId === e.occupantId)?.id === edit.targetSeatId);
    if (!target || (target.occupantId && target.occupantId !== edit.occupantId && !pendingLeave)) {
      throw new DomainError("seat_occupied", "只能移动到空座");
    }
    draft.rosterEdits.push({ type: "move", occupantId: edit.occupantId, targetSeatId: edit.targetSeatId });
  } else {
    throw new DomainError("bad_edit", `未知成员操作：${edit.type}`);
  }
  return draft;
}

/* ------------------------------------------------- P01/P02 位置轮转与预览 */

// P01：无人变动时的常规轮转（不确认不生效，仅供预览/自动提案）。
export function automaticNextPositions(session) {
  const seated = activeSeats(session);
  const n = session.capacity;
  const nextOf = (seatId) => {
    for (let step = 1; step <= n; step++) {
      const sid = ((seatId - 1 + step) % n) + 1;
      if (seated.includes(sid)) return sid;
    }
    return null;
  };
  const btn = session.positions.buttonSeatId;
  const sb = session.positions.smallBlindSeatId;
  const bb = session.positions.bigBlindSeatId;
  if (seated.length === 2) {
    // P03：两人名单不变 → BTN/SB 与 BB 互换
    const other = seated.find((s) => s !== btn);
    return { buttonSeatId: other, smallBlindSeatId: other, bigBlindSeatId: btn, source: "automatic", confirmed: false };
  }
  const newBtn = nextOf(btn);
  const newSb = nextOf(newBtn);
  const newBb = nextOf(newSb);
  return {
    buttonSeatId: newBtn, smallBlindSeatId: newSb, bigBlindSeatId: newBb,
    source: "automatic", confirmed: false,
  };
}

// P02：名单变化后的候选预览（预填，需用户确认现场位置）。
// rosterEdits：本手结算草稿中已排队、尚未应用的成员操作——预览基于变化后的名单。
export function previewNextPositions(session, rosterEdits = []) {
  const sim = clone(session);
  for (const edit of rosterEdits) {
    if (edit.type === "leave") {
      const seat = sim.seats.find((s) => s.occupantId === edit.occupantId);
      if (seat) seat.occupantId = null;
    } else if (edit.type === "move") {
      const from = sim.seats.find((s) => s.occupantId === edit.occupantId);
      const to = sim.seats.find((s) => s.id === edit.targetSeatId);
      if (from) from.occupantId = null;
      if (to) to.occupantId = edit.occupantId;
    } else if (edit.type === "enter") {
      const seat = sim.seats.find((s) => s.id === edit.seatId);
      if (seat) seat.occupantId = edit.occupantId;
      sim.occupants[edit.occupantId] = { id: edit.occupantId, status: "active", confirmedChips: edit.chips, profileName: null };
    }
  }
  return previewPositionsOf(sim);
}

function previewPositionsOf(session) {
  const seated = activeSeats(session);
  const oldBbSeat = session.positions.bigBlindSeatId;
  const oldSbSeat = session.positions.smallBlindSeatId;
  const n = session.capacity;
  if (seated.length === 2) {
    // P03：>=3 转 2
    const oldBbStillIn = seated.includes(oldBbSeat);
    let candBb;
    if (oldBbStillIn) {
      // 上手 BB 仍在 → 新 BB 建议另一名玩家，避免连续付 BB
      candBb = seated.find((s) => s !== oldBbSeat);
    } else {
      candBb = null;
      for (let step = 0; step < n; step++) {
        const sid = ((oldBbSeat - 1 + step) % n) + 1;
        if (seated.includes(sid)) { candBb = sid; break; }
      }
    }
    const candBtnSb = seated.find((s) => s !== candBb);
    return { buttonSeatId: candBtnSb, smallBlindSeatId: candBtnSb, bigBlindSeatId: candBb, source: "manual", confirmed: false };
  }
  const nextOf = (from) => {
    if (from == null) return null;
    for (let step = 1; step <= n; step++) {
      const sid = ((from - 1 + step) % n) + 1;
      if (seated.includes(sid)) return sid;
    }
    return null;
  };
  const candBb = nextOf(oldBbSeat);
  // 候选 SB：旧 BB 座位上的原玩家仍在则优先；否则允许空小盲
  const oldBbOcc = activeOccupantAt(session, oldBbSeat);
  const candSb = oldBbOcc && seated.includes(oldBbSeat) ? oldBbSeat : null;
  // 候选 BTN：参考旧 SB 座位（仍在且不与 BB 同座），否则 BB 顺时针下一位
  const oldSbStillIn = oldSbSeat != null && seated.includes(oldSbSeat);
  const candBtn = oldSbStillIn && oldSbSeat !== candBb ? oldSbSeat : nextOf(candBb);
  return {
    buttonSeatId: candBtn,
    smallBlindSeatId: candSb,
    bigBlindSeatId: candBb,
    source: "manual",
    confirmed: false,
  };
}

export function applyDraftPositions(draft, positions) {
  draft.positions = {
    buttonSeatId: positions.buttonSeatId,
    smallBlindSeatId: positions.smallBlindSeatId ?? null,
    bigBlindSeatId: positions.bigBlindSeatId,
    source: positions.source || "manual",
    confirmed: !!positions.confirmed,
  };
  return draft;
}

export function setDraftBlinds(draft, sb, bb, anteEach) {
  const level = { sb: Number(sb), bb: Number(bb), anteEach: Number(anteEach ?? 0) };
  if (!Number.isSafeInteger(level.sb) || level.sb <= 0
    || !Number.isSafeInteger(level.bb) || level.bb < level.sb
    || !Number.isSafeInteger(level.anteEach) || level.anteEach < 0) {
    throw new DomainError("bad_blinds", "盲注须为正整数且 bb≥sb，前注 ≥0");
  }
  draft.blindLevel = level;
  return draft;
}

export function setDraftLearning(draft, enabled) {
  draft.learningEnabled = !!enabled;
  return draft;
}

export function setDraftIcm(draft, icm) {
  const scope = icm?.scope === "final_table" ? "final_table" : "off";
  const payouts = Array.isArray(icm?.payouts) ? icm.payouts.map(Number) : [];
  if (scope === "final_table"
    && (!payouts.length || payouts.some((x) => !Number.isFinite(x) || x < 0))) {
    throw new DomainError("bad_icm", "奖金结构无效");
  }
  draft.icm = { scope, payouts, rosterConfirmed: !!icm?.rosterConfirmed };
  return draft;
}

/* ------------------------------------------------- S02 结算并下一手（事务） */

// 同步部分：校验草稿并克隆状态。prepared 由调用方在后端 prepare 成功后传入
// completeCommit；prepare 失败则整笔保持待确认（契约 S02 末段）。
export function beginCommit(session, opts) {
  requirePhase(session, ["settling"]);
  const draft = session.settlementDraft;
  const check = validateSettlement(session, draft);
  if (!check.ok) {
    const err = new DomainError("settlement_invalid", check.errors.join("；"));
    err.details = check;
    throw err;
  }
  const nextDraft = opts?.nextHandDraft || session.nextHandDraft || emptyNextHandDraft(session);
  const tx = {
    transactionId: newId("t"),
    sourceHandId: draft.sourceHandId,
    sourceRevision: draft.sourceRevision,
    startingSession: clone(session),
    settlement: clone(draft),
    nextDraft: clone(nextDraft),
  };
  return tx;
}

// prepared: {context, mapping}（后端 /api/table/prepare 返回）。
// 返回新会话（原子替换）；调用方持久化成功后才替换可见状态（S02 步骤 7）。
export function completeCommit(session, tx, prepared) {
  if (session.currentHand.handId !== tx.sourceHandId) {
    throw new DomainError("stale_commit", "本手已变化，请重新核对结算");
  }
  // lastCommit 幂等：同源手已提交过 → 拒绝重复执行
  if (session.lastCommit && session.lastCommit.sourceHandId === tx.sourceHandId) {
    throw new DomainError("already_committed", "本手已结算，重复提交被忽略");
  }
  const next = clone(session);
  const hand = next.currentHand;
  const summary = {
    handId: hand.handId,
    handNumber: hand.handNumber,
    context: clone(hand.context),
    finalChipsByOccupant: Object.fromEntries(
      tx.settlement.rows.map((r) => [r.occupantId, r.finalChips])),
    recordQuality: hand.recordQuality,
    adjustmentReason: tx.settlement.adjustmentReason,
    positionSource: next.positions.source,
    settledAt: new Date().toISOString(),
  };
  // C-学习/A-04：开启学习且牌谱完整（真实摊牌走完）才创建记录任务；
  // manual_close 牌谱不完整，不能接续对手画像（R03）。
  if (hand.context.learning_enabled && hand.recordQuality === "complete") {
    next.learningJobs ??= {};
    next.learningJobs[hand.handId] = {
      payload: {
        protocol_version: 2,
        hand_id: hand.handId,
        context: clone(hand.context),
        hero_cards: clone(hand.heroCards),
        ops: clone(hand.ops),
      },
      status: "pending",
      lastError: null,
    };
  }
  next.recentHands.push(summary);
  if (next.recentHands.length > 100) next.recentHands.shift();

  // 3：应用确认余额；0 筹码者淘汰
  for (const row of tx.settlement.rows) {
    const occ = next.occupants[row.occupantId];
    if (!occ) continue;
    occ.confirmedChips = row.finalChips;
    if (row.finalChips === 0 && occ.status === "active") occ.status = "eliminated";
  }
  // 4：应用名单变化
  let heroGone = false;
  for (const edit of tx.nextDraft.rosterEdits) {
    if (edit.type === "leave") {
      const occ = next.occupants[edit.occupantId];
      if (occ) occ.status = edit.reason === "eliminated" ? "eliminated" : "departed";
      const seat = next.seats.find((s) => s.occupantId === edit.occupantId);
      if (seat) seat.occupantId = null;
      if (edit.occupantId === next.heroOccupantId) heroGone = true;
    } else if (edit.type === "move") {
      const from = next.seats.find((s) => s.occupantId === edit.occupantId);
      const to = next.seats.find((s) => s.id === edit.targetSeatId);
      if (from) from.occupantId = null;
      if (to) to.occupantId = edit.occupantId;
    } else if (edit.type === "enter") {
      const seat = next.seats.find((s) => s.id === edit.seatId);
      if (!seat || seat.occupantId) throw new DomainError("seat_occupied", "入座目标非空座");
      seat.occupantId = edit.occupantId;
      next.occupants[edit.occupantId] = {
        id: edit.occupantId, status: "active",
        confirmedChips: edit.chips, profileName: edit.name || null,
      };
    }
  }
  const activeCount = activeSeats(next).length;

  next.blindLevel = { ...tx.nextDraft.blindLevel };
  next.learningEnabled = tx.nextDraft.learningEnabled;
  next.icm = clone(tx.nextDraft.icm);
  next.positions = tx.nextDraft.positions
    ? clone(tx.nextDraft.positions)
    : automaticNextPositions({ ...next, positions: next.positions });

  const lastCommit = {
    sourceHandId: tx.sourceHandId,
    transactionId: tx.transactionId,
    nextHandId: null,
  };

  if (heroGone || next.occupants[next.heroOccupantId]?.status !== "active" || activeCount < 2) {
    // M08/C15：英雄离场或不足 2 人 → 保存本手并结束
    next.phase = "ended";
    next.currentHand = null;
    next.settlementDraft = null;
    next.nextHandDraft = null;
    next.lastCommit = lastCommit;
    next.sessionRevision += 1;
    return next;
  }

  // 6：生成下一手
  const validated = normalizePositions(next.positions, next.seats, next.occupants, next.blindLevel.bb);
  next.positions = validated;
  next.previousBigBlindOccupantId = activeOccupantAt(session, session.positions.bigBlindSeatId)?.id || null;
  next.previousBigBlindSeatId = session.positions.bigBlindSeatId;
  next.handNumber += 1;
  next.currentHand = makeHandSnapshot(next, next.handNumber);
  next.currentHand.context = buildHandContext(next, next.handNumber);
  next.settlementDraft = null;
  next.nextHandDraft = null;
  next.phase = "ready";
  lastCommit.nextHandId = next.currentHand.handId;
  next.lastCommit = lastCommit;
  next.sessionRevision += 1;
  // 手号推进后 currentHand.revision 重新从 1 计（新手）
  return next;
}

/* -------------------------------------------------------------- 辅助校验 */

// A-04/A-05：学习记录任务的查询与状态回写。发送方（app 层）只负责
// fetch；重试永远用入队时的原 payload（契约：异步重试只能用这一份）。
export function pendingLearningJobs(session) {
  return Object.entries(session.learningJobs || {})
    .filter(([, job]) => job.status !== "success")
    .map(([handId, job]) => ({ handId, payload: job.payload, status: job.status,
                               lastError: job.lastError }));
}

export function markLearningJob(session, handId, status, lastError = null) {
  const job = session.learningJobs?.[handId];
  if (!job) return;
  if (status === "success") {
    delete session.learningJobs[handId];
  } else {
    job.status = status;
    job.lastError = lastError;
  }
}

function requirePhase(session, phases) {
  if (!phases.includes(session.phase)) {
    throw new DomainError("bad_phase", `当前阶段 ${session.phase} 不允许此操作（需 ${phases.join("/")}）`);
  }
}

export function rolesForSeat(session, seatId) {
  const pos = session.positions;
  const roles = [];
  if (pos.buttonSeatId === seatId) roles.push("BTN");
  if (pos.smallBlindSeatId === seatId) roles.push("SB");
  if (pos.bigBlindSeatId === seatId) roles.push("BB");
  return roles;
}
