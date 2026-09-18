// 锦标赛连续牌桌 · 界面控制器（P3/P4）。
// 领域迁移：session.js；持久化：session-storage.js；请求守卫：request-coordinator.js。
// 本文件只做事件、渲染与调用编排（README 第 8 节职责）。
"use strict";

import {
  createSession, chipsFromBB, chipsToBBText,
  setHeroCards, pushOp, undoLastOp, replayHand, manualCloseHand,
  enterSettling, cancelSettlement,
  buildSettlementDraft, editSettlementRow, confirmAllCurrentValues,
  validateSettlement, beginCommit, completeCommit,
  emptyNextHandDraft, applyRosterEdit, applyDraftPositions, setDraftBlinds,
  previewNextPositions, automaticNextPositions, rolesForSeat,
  heroSeatOf, rangePositionFor, buildHandContext, DomainError,
  pendingLearningJobs, markLearningJob, previewRoster, assertCommitCurrent, activeSeats,
} from "./session.js";
import * as Store from "./session-storage.js";
import { createCoordinator } from "./request-coordinator.js";

/* --------------------------------------------------------------- 基础工具 */

const $ = (sel) => document.querySelector(sel);
const RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"];
const SUITS = ["s", "h", "d", "c"];
const SUIT_GLYPH = { s: "♠", h: "♥", d: "♦", c: "♣" };
const IS_RED = { s: false, h: true, d: true, c: false };
const STREET_LABEL = { preflop: "翻牌前", flop: "翻牌", turn: "转牌", river: "河牌" };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtChips(n) { return Number(n).toLocaleString("zh-CN"); }
function fmtNum(n) { return (Math.round((Number(n) || 0) * 10) / 10).toFixed(1); }
function actionAmount(chips) {
  return rt.unit === "bb" ? `${bbInputValue(chips)} BB / ${fmtChips(chips)} 筹码`
    : `${fmtChips(chips)} 筹码 / ${bbInputValue(chips)} BB`;
}
function bbInputValue(chips) {
  return String(Number((chips / rt.session.blindLevel.bb).toFixed(6)));
}

/* ---------------------------------------------------------------- 运行时 */

const rt = {
  session: null,
  coord: null,
  view: null,
  advice: null,
  adviceState: "idle",   // idle | pending | updated | failed
  pendingOp: null,
  gridMode: null,
  heroPicks: [],
  boardPicks: [],
  reviewingHand: false,
  saveState: "", expectedRaw: null, conflict: false, committing: false, error: "", setupUnit: "bb",
  unit: "bb",
  setup: { occupied: new Map(), heroSeatId: null, buttonSeatId: null, sbSeatId: null, bbSeatId: null },
  setupPreview: null,
  nextPositionsConfirmed: false,
  advancedBound: false,
};

/* -------------------------------------------------------------- 启动恢复 */

function init() {
  bindGlobal();
  const loaded = Store.loadSession();
  rt.expectedRaw = loaded.raw ?? null;
  if (loaded.status === "ok") {
    rt.session = loaded.session;
    rt.coord = createCoordinator(rt.session, playerHeaders);
    rt.unit = rt.session.displayUnit || "bb";
    resyncAll();
    resumeIfPossible();
    syncLearningJobs();     // A-04：恢复页面时补发未完成的学习记录
    return;
  }
  showSetupWizard(loaded);
}

function resumeIfPossible() {
  const s = rt.session;
  if (s.phase === "ended") { renderAll(); return; }
  if (s.currentHand && s.currentHand.heroCards[0] && s.currentHand.heroCards[1]) {
    renderAll();
    refreshView();        // L-01：同一 handId/context/ops 重新回放；引擎无状态不重复扣盲
    if (window.matchMedia("(max-width: 767px)").matches) $("#deck-panel").open = false;
  } else {
    renderAll();
  }
}

function showSetupWizard(loaded) {
  $("#layout").dataset.phase = "setup";
  $("#col-table").hidden = false;
  $("#console").hidden = false;
  $("#advice-panel").hidden = false;
  $("#advanced-panel").hidden = true;
  $("#history-panel").hidden = true;
  $("#nextround-panel").hidden = true;
  $("#setup-panel").hidden = false;
  if (loaded && (loaded.status === "corrupt" || loaded.status === "future")) {
    setupError(loaded.status === "future"
      ? "本地会话来自更新版本的 schema，已保留原文本，可导出后新建牌桌。"
      : "本地会话数据损坏，已保留原文本，可导出后新建牌桌。");
    const exportBtn = document.createElement("button");
    exportBtn.type = "button";
    exportBtn.className = "ghost-btn";
    exportBtn.textContent = "导出原文本";
    exportBtn.addEventListener("click", () => downloadRaw(loaded.raw || ""));
    $("#tg-create").parentElement.insertBefore(exportBtn, $("#tg-create"));
  }
  const legacy = Store.loadLegacyConfig();
  if (legacy) {
    $("#tg-capacity").value = legacy.capacity;
    $("#tg-sb").value = legacy.sb;
    $("#tg-bb").value = legacy.bb;
    $("#tg-ante").value = legacy.anteEach;
    $("#tg-unit").value = "chips";
    rt.setupUnit = "chips";
    $("#tg-default-chips").value = legacy.defaultChips;
  }
  rt.setup = {occupied: new Map(), heroSeatId: 1, buttonSeatId: null};
  renderSetupSeats();
  renderDeck();
}

function downloadRaw(text) {
  const blob = new Blob([text], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "paishi-session-backup.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------ 建桌向导 */

function renderSetupSeats() {
  const capacity = Number($("#tg-capacity").value);
  if (rt.setup.capacity !== capacity) {
    rt.setup.capacity = capacity;
    rt.setup.occupied = new Map(Array.from({length: capacity}, (_, i) => [i + 1, {chips: null, name: ""}]));
    rt.setup.heroSeatId = 1;
    rt.setup.buttonSeatId = null;
  }
  const button = rt.setup.buttonSeatId;
  $("#setup-position-status").textContent = button ? `${button}号座 · BTN` : "待选择";
  const wrap = $("#tg-seats");
  wrap.innerHTML = `<div class="setup-table" aria-label="选择庄位">${Array.from({length: capacity}, (_, i) => {
    const sid = i + 1, xy = seatXY(i + capacity / 2, capacity);
    return `<button type="button" class="setup-seat ${button === sid ? "picked" : ""}" data-button="${sid}" style="left:${xy.x}%;top:${xy.y}%" aria-pressed="${button === sid}">
      ${sid === 1 ? "你 · 1号座" : sid + "号座"}<small>${button === sid ? "庄位 BTN" : rt.setup.occupied.has(sid) ? "在座" : "空座"}</small></button>`;
  }).join("")}</div>
  <details id="setup-roster"><summary>调整其他玩家 / 空座</summary>
  ${Array.from({length: capacity - 1}, (_, i) => i + 2).map(sid => {
    const occ = rt.setup.occupied.get(sid);
    return `<div class="tg-seat-row"><label><input type="checkbox" data-occupied="${sid}" ${occ ? "checked" : ""}> ${sid}号座</label>
      <label>筹码 <input type="number" data-chips="${sid}" value="${occ?.chips ?? ""}" placeholder="同我的筹码（估算）" ${occ ? "" : "disabled"}></label>
      ${$("#tg-learning").checked ? `<input data-name="${sid}" value="${esc(occ?.name || "")}" placeholder="代号（可选）" aria-label="${sid}号座代号">` : ""}</div>`;
  }).join("")}</details>`;
  wrap.querySelectorAll("[data-button]").forEach(el => el.onclick = () => {
    rt.setup.buttonSeatId = Number(el.dataset.button); renderSetupSeats();
  });
  wrap.querySelectorAll("[data-occupied]").forEach(el => el.onchange = () => {
    const sid = Number(el.dataset.occupied);
    if (el.checked) rt.setup.occupied.set(sid, {chips: null, name: ""});
    else rt.setup.occupied.delete(sid);
    renderSetupSeats(); $("#setup-roster").open = true;
  });
  wrap.querySelectorAll("[data-chips]").forEach(el => el.oninput = () => {
    rt.setup.occupied.get(Number(el.dataset.chips)).chips = el.value === "" ? null : Number(el.value);
  });
  wrap.querySelectorAll("[data-name]").forEach(el => el.oninput = () => {
    rt.setup.occupied.get(Number(el.dataset.name)).name = el.value;
  });
  updateSetupAmountHint();
  renderWorkflow();
}

function amountInput(text, unit, bb, confirmRound = false) {
  if (String(text).trim() === "") throw new DomainError("bad_amount", "请填写筹码");
  if (unit === "chips") {
    const chips = Number(text);
    if (!Number.isSafeInteger(chips) || chips < 0) throw new DomainError("bad_amount", "筹码须为非负整数");
    return chips;
  }
  const result = chipsFromBB(text, bb);
  if (!result.ok) throw new DomainError("bad_amount", "BB 数量无效");
  if (result.changed && confirmRound && !window.confirm(`换算为 ${result.exactText} 筹码，取整为 ${result.chips} 筹码？`)) throw new DomainError("cancelled", "已取消取整");
  return result.chips;
}

function updateSetupAmountHint() {
  try {
    const chips = amountInput($("#tg-default-chips").value, $("#tg-unit").value, Number($("#tg-bb").value));
    $("#tg-unit-hint").textContent = `${fmtChips(chips)} 筹码 · ${chipsToBBText(chips, Number($("#tg-bb").value))} BB`;
  } catch { $("#tg-unit-hint").textContent = ""; }
}



function setupError(msg) {
  const el = $("#setup-error");
  el.textContent = msg || "";
  el.hidden = !msg;
}

function collectSetup(confirmRound = false) {
  const bb = Number($("#tg-bb").value), capacity = Number($("#tg-capacity").value);
  const ownChips = amountInput($("#tg-default-chips").value, $("#tg-unit").value, bb, confirmRound);
  const entries = [...rt.setup.occupied].map(([sid, occ]) => ({
    seatId: sid, chips: sid === 1 ? ownChips : (occ.chips ?? ownChips),
    name: occ.name || null, estimated: sid !== 1 && occ.chips === null,
  }));
  const button = rt.setup.buttonSeatId;
  if (!button) throw new DomainError("bad_button", "请点选本手庄位");
  const order = Array.from({length: capacity}, (_, i) => (button + i) % capacity + 1).filter(sid => rt.setup.occupied.has(sid));
  return {
    capacity, entries, heroSeatId: 1, buttonSeatId: button,
    sbSeatId: entries.length === 2 ? button : order[0],
    bbSeatId: entries.length === 2 ? order.find(sid => sid !== button) : order[1],
    blindLevel: {sb: Number($("#tg-sb").value), bb, anteEach: Number($("#tg-ante").value)},
    displayUnit: $("#tg-unit").value, learningEnabled: $("#tg-learning").checked,
    icm: parsePayouts($("#tg-payouts").value),
  };
}

function parsePayouts(text) {
  const t = String(text || "").trim();
  if (!t) return { scope: "off", payouts: [], rosterConfirmed: false };
  const arr = t.split(/[，,]/).map((x) => Number(x.trim()));
  if (!arr.length || arr.some((x) => !Number.isFinite(x) || x < 0)) {
    throw new DomainError("bad_icm", "奖金结构须为逗号分隔的非负数");
  }
  return { scope: "final_table", payouts: arr, rosterConfirmed: true };
}

function onPreviewTable() { return onConfirmTable(); }

async function onConfirmTable() {
  if (rt.committing || rt.conflict) return;
  setupError("");
  try {
    const session = createSession(collectSetup(true));
    const snapshot = JSON.stringify(collectSetup());
    rt.committing = true;
    $("#tg-create").disabled = true;
    await prepareContext(session);
    if (JSON.stringify(collectSetup()) !== snapshot) throw new Error("设置已变化，请重新开始");
    if (rt.expectedRaw && !rt.session && !window.confirm("替换已保存的牌桌？")) return;
    await persistAtomic(session);
    rt.session = session;
    rt.coord = createCoordinator(session, playerHeaders);
    rt.unit = session.displayUnit;
    renderAll();
  } catch (e) { setupError(`建桌失败：${e.message}`); }
  finally { rt.committing = false; $("#tg-create").disabled = false; }
}

async function prepareContext(session, handId, handNumber) {
  const hand = session.currentHand;
  return prepareRaw({
    protocol_version: 2,
    hand_id: handId || hand.handId,
    hand_number: handNumber || hand.handNumber,
    context: hand ? hand.context : buildHandContext(session, handNumber || 1),
  });
}

async function prepareRaw(body) {
  const res = await fetch("/api/table/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

/* ------------------------------------------------------------- 保存状态 */

function playerHeaders() {
  // 27/A-04：匿名命名空间随头发送（与 logic.js 的 AppLogic.playerId 同一
  // localStorage 键 paishi_uid；本页不加载 logic.js，这里内联同规则）。
  let uid = "local";
  try {
    uid = localStorage.getItem("paishi_uid") || "local";
    if (uid === "local") {
      uid = (globalThis.crypto && globalThis.crypto.randomUUID)
        ? globalThis.crypto.randomUUID() : `u-${Date.now()}`;
      localStorage.setItem("paishi_uid", uid);
    }
  } catch (_e) { /* 隐私模式等：回退 local */ }
  return { "X-Player-Id": uid };
}

let learningSyncing = false;

// A-04/A-05：补发学习记录。用入队原 payload；关闭学习时暂停（契约：
// 重新开启后才允许重试）；409 保留任务不标失败。
async function syncLearningJobs() {
  if (!rt.session || learningSyncing || rt.conflict || rt.committing) return;
  const sourceSession = rt.session.sessionId;
  const jobs = pendingLearningJobs(rt.session);
  renderLearningStatus();
  if (!jobs.length || !rt.session.learningEnabled) return;
  learningSyncing = true;
  for (const job of jobs) {
    if (rt.session.sessionId !== sourceSession || rt.conflict || !rt.session.learningEnabled) break;
    try {
      const res = await fetch("/api/hand/v2/record", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...playerHeaders() },
        body: JSON.stringify(job.payload),
      });
      const data = await res.json().catch(() => ({}));
      if (rt.session.sessionId !== sourceSession || rt.conflict) break;
      if (res.status === 409) continue;      // 学习已关闭：暂停，保留任务
      if (res.ok && data.recorded) {
        markLearningJob(rt.session, job.handId, "success");
      } else {
        markLearningJob(rt.session, job.handId, "failed",
          data.detail || `HTTP ${res.status}`);
      }
    } catch (e) {
      if (rt.session.sessionId === sourceSession && !rt.conflict) markLearningJob(rt.session, job.handId, "failed", e.message);
    }
  }
  learningSyncing = false;
  persist();
  renderLearningStatus();
}

function renderLearningStatus() {
  const el = $("#learning-status");
  if (!el || !rt.session) return;
  const jobs = pendingLearningJobs(rt.session);
  const paused = jobs.length && !rt.session.learningEnabled;
  el.hidden = !jobs.length;
  el.innerHTML = jobs.length
    ? `${paused ? "有学习记录待发送（学习已关闭）"
        : jobs.some((j) => j.status === "failed")
        ? "学习记录发送失败，可重试"
        : "正在发送学习记录…"}
      ${paused ? "" : `<button type="button" class="ghost-btn" data-action="retry-learning">重试</button>`}`
    : "";
  const btn = el.querySelector('[data-action="retry-learning"]');
  if (btn) btn.addEventListener("click", () => syncLearningJobs());
}

function persist(candidate = rt.session) {
  if (!candidate || rt.conflict) return false;
  const result = Store.saveSession(candidate, rt.expectedRaw);
  rt.saveState = result.ok ? "saved" : "error";
  if (result.ok) rt.expectedRaw = result.raw;
  else if (result.error === "conflict") showConflict();
  setSaveLabel();
  return result.ok;
}

async function persistAtomic(candidate) {
  const save = () => { if (!persist(candidate)) throw new Error("保存失败，尚未进入下一手。请导出备份后重试"); };
  if (navigator.locks) await navigator.locks.request("paishi-session-write", save);
  else save();
}

function showConflict() {
  rt.conflict = true;
  rt.coord?.resync(rt.session);
  rt.error = "其他标签页已更新牌桌，本页已暂停。请载入最新记录。";
  const banner = $("#conflict-banner");
  banner.hidden = false;
}

function setSaveLabel() {
  const label = rt.saveState === "saved" ? "已保存"
    : rt.saveState === "error" ? "保存失败（数据仅在本页）" : "";
  for (const el of [$("#save-state"), $("#caption-save")]) if (el) el.textContent = label;
}

/* ------------------------------------------------------------ 每手流程 */

function viewPayload(ops) {
  const hand = rt.session.currentHand;
  return {
    protocol_version: 2,
    hand_id: hand.handId,
    context: hand.context,
    hero_cards: hand.heroCards,
    ops,
    iterations: hand.context.participants.length >= 7 ? 3000 : 5000,
  };
}

function refreshView() {
  if (rt.conflict) return;
  rt.error = "";
  rt.coord.resync(rt.session);
  rt.coord.submitView(rt.session, viewPayload(rt.session.currentHand.ops), {
    onAccept(body) {
      rt.view = body;
      rt.advice = null;
      rt.adviceState = "idle";
      if (rt.session.phase === "ready") rt.session.phase = "playing";
      if (body.hand_over && rt.session.phase === "playing" && !rt.reviewingHand) enterSettlePhase(body);
      renderAll();
      maybeAdvice();
    },
    onError(err) {
      rt.error = `局面更新失败：${err.message}`;
      renderConsole();
      renderDeck();
    },
  });
  renderAll();
}

function submitAction(op) {
  if (rt.conflict || rt.committing || rt.coord.viewPending()) return;
  rt.error = ""; //          // S04.1：不排队第二个未验证动作
  const hand = rt.session.currentHand;
  rt.pendingOp = op;
  rt.coord.invalidateAdvice();                 // S04.3：旧推演立刻失效
  rt.advice = null;
  rt.adviceState = "idle";
  rt.coord.submitView(rt.session, viewPayload([...hand.ops, op]), {
    onAccept(body) {
      pushOp(rt.session, op);                  // 验证通过才落地（revision 推进）
      rt.view = body;
      rt.pendingOp = null;
      persist();
      if (body.hand_over) enterSettlePhase(body);
      else maybeAdvice();
      renderAll();
      if (op.op === "board") {
        if (window.matchMedia("(max-width: 767px)").matches) $("#deck-panel").open = false;
        $('#action-area button:not([disabled])')?.focus({preventScroll: true});
      }
    },
    onError(err) {
      rt.pendingOp = null;                     // 待确认动作从未落地（S-13）
      rt.error = `动作未被接受：${err.message}`;
      renderAll();
    },
  });
  renderAll();
}

function onUndo() {
  rt.reviewingHand = false;
  rt.coord.invalidateAdvice();
  undoLastOp(rt.session);
  rt.view = null;
  rt.advice = null;
  rt.adviceState = "idle";
  persist();
  refreshView();
}

function onReplayHand() {
  if (!window.confirm("重录本手？动作与底牌清空；庄位、手数与已确认余额不变。")) return;
  replayHand(rt.session);
  rt.reviewingHand = false;
  rt.coord.resync(rt.session);
  rt.pendingOp = null; rt.error = "";
  $("#deck-panel").open = true;
  rt.view = null;
  rt.advice = null;
  rt.adviceState = "idle";
  rt.heroPicks = [];
  rt.boardPicks = [];
  rt.gridMode = null;
  persist();
  renderAll();
}

function onManualClose() {
  const reason = window.prompt("说明本手记录不完整的原因（将随本手保存）：");
  if (reason === null) return;
  try {
    manualCloseHand(rt.session, reason);
    rt.coord.resync(rt.session);
    rt.pendingOp = null;
  } catch (e) {
    setStatus(e.message);
    return;
  }
  persist();
  renderAll();
}

function enterSettlePhase(view) {
  rt.reviewingHand = false;
  try {
    enterSettling(rt.session, view ? view.settlement_preview.rows : []);
  } catch (e) {
    setStatus(e.message);
    return;
  }
  rt.nextPositionsConfirmed = false;
  if (!rt.session.nextHandDraft) {
    rt.session.nextHandDraft = emptyNextHandDraft(rt.session);
  }
  persist();
  renderAll();
}

/* ------------------------------------------------------------- 结算流程 */

function renderSettle() {
  const area = $("#settle-area");
  const s = rt.session;
  if (s.phase !== "settling" || !s.settlementDraft) {
    area.hidden = true;
    return;
  }
  area.hidden = false;
  const draft = s.settlementDraft;
  const hand = s.currentHand;
  const v = validateSettlement(s, draft);
  const rows = draft.rows.map((row) => {
    const part = hand.context.participants.find((p) => p.seat_id === row.seatId);
    const occ = s.occupants[row.occupantId];
    const isHero = row.occupantId === s.heroOccupantId;
    const val = row.finalChips === null ? "" : String(row.finalChips);
    const srcTag = row.source === "pending" || row.finalChips === null ? '<span class="src-tag warn">待核对</span>'
      : row.source === "manual" ? '<span class="src-tag">手工</span>'
      : '<span class="src-tag ok">可核对</span>';
    return `<tr data-seat="${row.seatId}">
      <td>${row.seatId}号座${isHero ? " · 你" : ""}</td>
      ${s.learningEnabled ? `<td>${esc(occ?.profileName || "—")}</td>` : ""}
      <td>${fmtChips(part.starting_chips)}</td>
      <td>${srcTag}</td>
      <td><input type="number" inputmode="numeric" class="settle-input" data-seat="${row.seatId}"
           value="${val}" placeholder="最终余额" aria-label="${row.seatId}号座最终实际余额"></td>
      <td>${row.finalChips === null ? "—" : chipsToBBText(row.finalChips, s.blindLevel.bb) + "BB"}</td>
    </tr>`;
  }).join("");
  const deltaLine = v.delta === 0
    ? `<span class="src-tag ok">总额守恒 ${fmtChips(v.finalSum)}</span>`
    : `<span class="src-tag warn">差额 ${v.delta > 0 ? "+" : ""}${fmtChips(v.delta)}（手前 ${fmtChips(v.startingSum)} → 填写 ${fmtChips(v.finalSum)}）</span>`;
  area.innerHTML = `
    <div class="group-title">核对本手结束筹码 <span class="tip">第 ${hand.handNumber} 手 · BB 换算按 ${s.blindLevel.bb}</span></div>
    <div class="settle-scroll"><table class="adv-table settle-table">
      <thead><tr><th>座位</th>${s.learningEnabled ? "<th>玩家</th>" : ""}<th>手前</th><th>来源</th><th>手后实际余额</th><th>BB</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="settle-tools">
      <button type="button" class="ghost-btn" data-action="confirm-current">确认当前填写值</button>
      <span id="settle-totals">${deltaLine}</span>
    </div>
    <div class="setup-row" id="settle-diff-row" ${v.delta === 0 ? "hidden" : ""}>
      <input type="text" id="settle-reason" placeholder="差额原因（现场漏记/初始录错等）" value="${esc(draft.adjustmentReason || "")}">
      <label class="switch-line"><input type="checkbox" id="settle-accept" ${draft.differenceAccepted ? "checked" : ""}> 我确认该差额</label>
    </div>
    <details id="roster-details"><summary>人员变动 / 校准庄位</summary><div id="roster-area"></div></details>
    <p class="form-error" id="settle-error" role="alert" hidden></p>
    <div class="settle-actions">
      <button type="button" class="primary-btn" id="settle-commit" data-action="settle-commit">确认余额，进入下一手</button>
      <button type="button" class="ghost-btn" data-action="settle-cancel">取消，继续纠正本手</button>
    </div>`;
  bindSettleEvents(area);
  renderRosterEditor(area.querySelector("#roster-area"));
  updateCommitLabel();
  if (needsCalibration(s)) area.querySelector("#roster-details").open = true;
}

function bindSettleEvents(area) {
  area.querySelectorAll(".settle-input").forEach(input => input.addEventListener("input", e => {
    const seatId = Number(input.dataset.seat), draft = rt.session.settlementDraft;
    const value = input.value === "" ? null : Number(input.value);
    const valid = value === null || (Number.isSafeInteger(value) && value >= 0);
    editSettlementRow(draft, seatId, valid ? value : null);
    input.setCustomValidity(valid ? "" : "余额须为非负整数");
    draft.differenceAccepted = false;
    rt.session.nextHandDraft.positions = null;
    persist();
    const check = validateSettlement(rt.session, draft);
    $("#settle-totals").textContent = draft.rows.some(r => r.finalChips === null) ? "有余额待核对" : `合计 ${fmtChips(check.finalSum)} · 差额 ${fmtChips(check.delta)}`;
    $("#settle-diff-row").hidden = check.delta === 0;
    $("#settle-accept").checked = false;
    input.closest("tr").lastElementChild.textContent = value === null || !valid ? "待核对" : chipsToBBText(value, rt.session.blindLevel.bb) + " BB";
    input.closest("tr").querySelector(".src-tag").textContent = value === null || !valid ? "待核对" : "手工";
    renderRosterEditor($("#roster-area"));
    renderSeats();
    updateCommitLabel();
    if (needsCalibration(rt.session)) $("#roster-details").open = true;
  }));
  area.querySelector('[data-action="confirm-current"]')?.addEventListener("click", () => {
    confirmAllCurrentValues(rt.session.settlementDraft); persist(); renderSettle();
  });
  area.querySelector("#settle-reason").addEventListener("input", e => {
    rt.session.settlementDraft.adjustmentReason = e.target.value; persist();
  });
  area.querySelector("#settle-accept").addEventListener("change", e => {
    rt.session.settlementDraft.differenceAccepted = e.target.checked; persist();
  });
  area.querySelector('[data-action="settle-cancel"]').addEventListener("click", () => {
    cancelSettlement(rt.session); rt.view = null; rt.coord.resync(rt.session);
    rt.reviewingHand = true;
    persist();
    if (rt.session.currentHand.heroCards.every(Boolean)) refreshView();
    else renderAll();
  });
  area.querySelector("#settle-commit").addEventListener("click", () => commitSettlement(area));
}

function needsCalibration(s) {
  return !!s.nextHandDraft?.rosterEdits.length || s.settlementDraft?.rows.some(r => r.finalChips === 0)
    || s.positions.smallBlindSeatId === null || !activeSeats(s).includes(s.positions.buttonSeatId);
}

function updateCommitLabel() {
  const s = rt.session;
  const button = $("#settle-commit");
  if (!button) return;
  button.disabled = rt.committing || s.settlementDraft.rows.some(row => row.finalChips === null);
  try {
    const candidate = previewRoster(s, s.nextHandDraft?.rosterEdits || []);
    const ending = activeSeats(candidate).length < 2 || candidate.occupants[s.heroOccupantId]?.status !== "active";
    $("#settle-commit").textContent = ending ? "确认余额，结束本桌" : "确认余额，进入下一手";
  } catch { /* 成员错误由结算校验呈现。 */ }
}

function showSettleError(msg) {
  const el = $("#settle-error");
  if (el) { el.textContent = msg || ""; el.hidden = !msg; }
}

function renderRosterEditor(container) {
  const s = rt.session;
  s.nextHandDraft ??= emptyNextHandDraft(s);
  let candidate;
  try { candidate = previewRoster(s, s.nextHandDraft.rosterEdits); }
  catch (e) { container.textContent = e.message; return; }
  container.innerHTML = `<div class="tg-seats" id="roster-rows">${candidate.seats.map(seat => {
    const occ = candidate.occupants[seat.occupantId];
    if (!occ || occ.status !== "active") return `<div class="tg-seat-row"><span>${seat.id}号座 · 空位</span>
      <input type="number" class="roster-enter-chips" data-seat="${seat.id}" placeholder="入座筹码" aria-label="${seat.id}号座入座筹码">
      ${s.nextHandDraft.learningEnabled ? `<input class="roster-enter-name" data-seat="${seat.id}" placeholder="代号（可选）">` : ""}
      <button type="button" class="ghost-btn roster-enter" data-seat="${seat.id}">入座</button></div>`;
    return `<div class="tg-seat-row"><span>${seat.id}号座${occ.id === s.heroOccupantId ? " · 你" : ""} · ${fmtChips(occ.confirmedChips)}</span>
      <button type="button" class="ghost-btn roster-leave" data-occ="${esc(occ.id)}">转桌离开</button>
      <button type="button" class="ghost-btn roster-move" data-occ="${esc(occ.id)}">换座</button></div>`;
  }).join("")}</div>
  <button type="button" class="ghost-btn" id="roster-reset">撤销本次人员变动</button><div id="position-preview"></div>`;
  const apply = edit => {
    try {
      applyRosterEdit(s, s.nextHandDraft, edit);
      rt.nextPositionsConfirmed = false; persist(); renderRosterEditor(container); renderNextSettings();
    } catch (e) { showSettleError(e.message); }
  };
  container.querySelectorAll(".roster-enter").forEach(btn => btn.onclick = () => {
    const sid = Number(btn.dataset.seat);
    apply({type: "enter", seatId: sid,
      chips: Number(container.querySelector(`.roster-enter-chips[data-seat="${sid}"]`).value),
      name: container.querySelector(`.roster-enter-name[data-seat="${sid}"]`)?.value || null});
  });
  container.querySelectorAll(".roster-leave").forEach(btn => btn.onclick = () => {
    if (window.confirm("确认该玩家转桌离开？")) apply({type: "leave", occupantId: btn.dataset.occ, reason: "table_transfer"});
  });
  container.querySelectorAll(".roster-move").forEach(btn => btn.onclick = () => {
    const target = window.prompt("移到哪个空座位？");
    if (target) apply({type: "move", occupantId: btn.dataset.occ, targetSeatId: Number(target)});
  });
  container.querySelector("#roster-reset").onclick = () => {
    s.nextHandDraft.rosterEdits = []; s.nextHandDraft.positions = null; persist(); renderRosterEditor(container);
  };
  renderPositionPreview(container.querySelector("#position-preview"));
  updateCommitLabel();
}

function renderPositionPreview(container) {
  const s = rt.session, draft = s.nextHandDraft, candidate = previewRoster(s, draft.rosterEdits);
  if (activeSeats(candidate).length < 2 || candidate.occupants[s.heroOccupantId]?.status !== "active") {
    container.textContent = "确认余额后结束本桌"; return;
  }
  const changed = needsCalibration(s);
  const preview = draft.positions || (changed ? previewNextPositions(s, draft.rosterEdits) : automaticNextPositions(s));
  container.innerHTML = `<p class="footnote">${changed ? "名单已变化，请核对现场位置" : "下一手自动轮转"} · 庄位 ${preview.buttonSeatId ?? "空"} · 小盲 ${preview.smallBlindSeatId ?? "空"} · 大盲 ${preview.bigBlindSeatId ?? "空"}</p>
    <div class="setup-row">
      <label>庄位 <input type="number" id="pos-btn" min="1" max="${s.capacity}" value="${preview.buttonSeatId ?? ""}"></label>
      <label>小盲（可留空）<input type="number" id="pos-sb" min="1" max="${s.capacity}" value="${preview.smallBlindSeatId ?? ""}"></label>
      <label>大盲 <input type="number" id="pos-bb" min="1" max="${s.capacity}" value="${preview.bigBlindSeatId ?? ""}"></label>
    </div><label class="switch-line"><input type="checkbox" id="pos-confirm" ${draft.positions?.confirmed ? "checked" : ""}> 已核对现场位置</label>`;
  const save = confirmed => {
    applyDraftPositions(draft, {buttonSeatId: Number($("#pos-btn").value),
      smallBlindSeatId: $("#pos-sb").value === "" ? null : Number($("#pos-sb").value),
      bigBlindSeatId: Number($("#pos-bb").value), source: "manual", confirmed});
    persist();
  };
  container.querySelectorAll('input[type="number"]').forEach(el => el.oninput = () => {
    $("#pos-confirm").checked = false; save(false);
  });
  container.querySelector("#pos-confirm").onchange = e => save(e.target.checked);
}

async function commitSettlement(area) {
  if (rt.committing || rt.conflict) return;
  rt.committing = true;
  area.querySelector("#settle-commit").disabled = true;
  try {
    const s = rt.session;
    confirmAllCurrentValues(s.settlementDraft);
    const tx = beginCommit(s);
    const committed = completeCommit(s, tx, null);
    if (committed.phase !== "ended") await prepareContext(committed);
    assertCommitCurrent(rt.session, tx);
    await persistAtomic(committed);
    rt.session = committed;
    rt.view = null; rt.advice = null; rt.error = "";
    rt.heroPicks = []; rt.boardPicks = []; rt.gridMode = null;
    rt.adviceState = "idle"; rt.nextPositionsConfirmed = false;
    rt.coord.resync(committed);
    $("#deck-panel").open = true;
    renderAll();
  } catch (e) { showSettleError(`结算未完成：${e.message}`); }
  finally {
    rt.committing = false;
    if (rt.session.phase === "settling") updateCommitLabel();
  }
  if (rt.session.phase !== "settling") syncLearningJobs();
}



/* ------------------------------------------------------------- 推演 */

function maybeAdvice() {
  const s = rt.session;
  const view = rt.view;
  if (!view || view.hand_over || !s.currentHand
    || !s.currentHand.heroCards[0]) { rt.adviceState = "idle"; return; }
  if (view.actor_seat_id !== heroSeatOf(s)) { rt.adviceState = "idle"; renderAdvice(); return; }
  rt.adviceState = "pending";
  renderAdvice();
  const capturedOps = s.currentHand.ops;
  rt.coord.submitAdvice(rt.session, viewPayload(capturedOps), {
    onResult(body) {
      rt.advice = body;
      rt.adviceState = "updated";
      renderAdvice();
    },
    onError() {
      rt.adviceState = "failed";
      renderAdvice();
    },
  });
}

/* ------------------------------------------------------------- 渲染 */

function resyncAll() { rt.coord?.resync(rt.session); }

function renderAll() {
  if (!rt.session) return;
  const s = rt.session;
  $("#layout").dataset.phase = s.phase;
  $("#col-table").hidden = false;
  $("#setup-panel").hidden = true;
  $("#setup-table-panel").hidden = true;
  $("#table-panel").hidden = false;
  $("#session-settings-panel").hidden = false;
  $("#console").hidden = s.phase === "ended";
  $("#advice-panel").hidden = s.phase !== "playing" && s.phase !== "ready";
  $("#advanced-panel").hidden = false;
  $("#history-panel").hidden = false;
  $("#nextround-panel").hidden = s.phase !== "settling";
  $("#deck-panel").hidden = s.phase === "settling" || s.phase === "ended";
  if (s.phase !== "settling") $("#settle-area").hidden = true;
  renderSessionBar();
  renderSessionSettings();
  renderTableCaption();
  renderSeats();
  renderBoard();
  renderConsole();
  renderAdvice();
  renderHistory();
  renderAdvanced();
  if (s.phase === "settling") renderSettle();
  renderNextSettings();
  renderIcm();
  renderDeck();
}

function renderWorkflow() {
  const s = rt.session;
  const choosingHero = s?.currentHand && !s.currentHand.heroCards.every(Boolean);
  const active = !s ? (rt.setup.buttonSeatId ? "setup-panel" : "setup-table-panel")
    : s.phase === "ended" ? null
    : s.phase === "settling" ? "console"
    : choosingHero || rt.gridMode === "board" ? "deck-panel" : "console";
  for (const el of document.querySelectorAll(".panel")) {
    el.classList.toggle("zone-focus", el.id === active);
  }
  const heroTurn = s?.phase === "playing" && rt.view && !rt.view.hand_over
    && rt.view.actor_seat_id === heroSeatOf(s) && !rt.coord?.viewPending();
  $("#advice-panel").classList.toggle("result-focus", !!heroTurn);
  $("#advice-panel").setAttribute("aria-busy", String(rt.adviceState === "pending"));
}

function renderSessionSettings() {
  const s = rt.session;
  const rows = [
    ["桌型", `${s.capacity} 人桌`],
    ["当前手数", `第 ${s.handNumber} 手`],
    ["我的座位", `${heroSeatOf(s) ?? "—"}号座`],
    ["庄位", `${s.positions.buttonSeatId}号座`],
    ["小盲 / 大盲", `${fmtChips(s.blindLevel.sb)} / ${fmtChips(s.blindLevel.bb)}`],
    ["每人前注", fmtChips(s.blindLevel.anteEach)],
    ["我的手前筹码", `${fmtChips(s.occupants[s.heroOccupantId]?.confirmedChips ?? 0)} 筹码`],
  ];
  $("#session-settings").innerHTML = rows.map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("");
}

function renderSessionBar() {
  const s = rt.session;
  const seat = heroSeatOf(s);
  const roleLabel = seat ? (rolesForSeat(s, seat).join("/") || rangePositionFor(s, seat) || "—") : "—";
  const occ = s.occupants[s.heroOccupantId];
  const phaseText = { ready: "待选底牌", playing: "进行中", settling: "待核对筹码", ended: "本桌已结束" }[s.phase] || "";
  $("#session-status").innerHTML = s.phase === "ended"
    ? `本桌已结束 · 共 ${s.handNumber} 手 · 你最终 ${fmtChips(occ?.confirmedChips ?? 0)} 筹码`
    : `本桌第 ${s.handNumber} 手 · ${phaseText} · 你：${seat ?? "—"}号座/${esc(roleLabel)} · ` +
      `盲注 ${s.blindLevel.sb}/${s.blindLevel.bb} · 每人前注 ${s.blindLevel.anteEach} · ` +
      `${fmtChips(occ?.confirmedChips ?? 0)} 筹码`;
  renderLearningStatus();
  const slot = $("#bar-main-slot");
  slot.innerHTML = "";
  if (s.phase === "playing" || s.phase === "ready") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost-btn";
    btn.textContent = "重录本手";
    btn.setAttribute("data-action", "replay-hand");
    btn.addEventListener("click", onReplayHand);
    slot.appendChild(btn);
  } else if (s.phase === "ended") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost-btn";
    btn.textContent = "新建牌桌";
    btn.setAttribute("data-action", "new-table");
    btn.addEventListener("click", onNewTable);
    slot.appendChild(btn);
  }
}

function onNewTable() {
  if (!window.confirm("新建牌桌？当前会话将被清除（可先在“更多设置”导出）。")) return;
  if (rt.conflict) return;
  Store.clearSession();
  location.reload();
}

function renderTableCaption() {
  const s = rt.session;
  if (!s || !s.currentHand) {
    $("#caption-hand").textContent = "";
    $("#caption-seat").textContent = "";
    return;
  }
  const seat = heroSeatOf(s);
  const roleLabel = seat ? (rolesForSeat(s, seat).join("/") || rangePositionFor(s, seat) || "—") : "—";
  const stage = s.phase === "settling" ? "待核对筹码"
    : s.phase === "playing" && rt.view ? (STREET_LABEL[rt.view.street] || "")
    : s.phase === "ready" ? "待选底牌" : "";
  $("#caption-hand").textContent = `本桌第 ${s.currentHand.handNumber} 手 · ${stage}`;
  $("#caption-seat").textContent =
    `你：${seat ?? "—"}号座 / ${roleLabel} · 盲注${s.blindLevel.sb}/${s.blindLevel.bb} · 每人前注${s.blindLevel.anteEach}`;
  $("#pot-num").textContent = rt.view
    ? `${fmtChips(rt.view.pot)} 筹码 / ${chipsToBBText(rt.view.pot, s.blindLevel.bb)} BB`
    : "—";
}

function seatXY(i, n) {
  const x = 50 + 37 * Math.sin((i / n) * 2 * Math.PI);
  const y = 50 - 40 * Math.cos((i / n) * 2 * Math.PI);
  return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 };
}

function renderSeats() {
  const s = rt.session;
  if (!s) return;
  const layer = $("#seat-layer");
  const viewSeats = rt.view ? new Map(rt.view.seats.map((x) => [x.seat_id, x])) : null;
  const html = [];
  for (const seat of s.seats) {
    const occ = seat.occupantId ? s.occupants[seat.occupantId] : null;
    const xy = seatXY(seat.id - (heroSeatOf(s) || 1) + s.capacity / 2, s.capacity);
    if (!occ || occ.status !== "active") {
      html.push(`<div class="seat empty" style="left:${xy.x}%;top:${xy.y}%" data-seat="${seat.id}">
        <div class="pos-tag">${seat.id}号座 ${s.positions.buttonSeatId === seat.id ? "BTN" : ""}</div><div class="stack muted">空位</div></div>`);
      continue;
    }
    const vs = viewSeats?.get(seat.id);
    const roles = rolesForSeat(s, seat.id);
    const roleTag = roles.length ? `<span class="pos-tag">${roles.join("/")}</span>` : "";
    const isHero = occ.id === s.heroOccupantId;
    const row = s.settlementDraft?.rows.find(r => r.occupantId === occ.id);
    const stack = row ? row.finalChips : vs ? vs.stack : occ.confirmedChips;
    const folded = vs?.folded;
    const allIn = vs?.all_in;
    const acting = s.phase === "playing" && rt.view && rt.view.actor_seat_id === seat.id && !rt.view.hand_over;
    html.push(`<div class="seat${isHero ? " hero" : ""}${acting ? " acting" : ""}${folded ? " folded" : ""}" style="left:${xy.x}%;top:${xy.y}%" data-seat="${seat.id}">
      <div class="pos-tag">${seat.id}号座${isHero ? " · 你" : ""} ${roleTag}</div>
      <div class="stack">${stack === null ? "待核对" : rt.unit === "chips" ? fmtChips(stack) : chipsToBBText(stack, s.blindLevel.bb) + " BB"}
        <span class="bb-tag">${stack === null ? "" : rt.unit === "chips" ? chipsToBBText(stack, s.blindLevel.bb) + " BB" : fmtChips(stack)}</span>${occ.chipsEstimated && !row ? '<span class="estimated-label">估算</span>' : ""}</div>
      ${allIn ? '<span class="badge badge-allin">全下</span>' : ""}
      ${folded ? '<span class="badge badge-fold">弃牌</span>' : ""}
      ${acting ? '<span class="badge acting-tag">待录入动作</span>' : ""}
    </div>`);
  }
  layer.innerHTML = html.join("");
  renderOpsLine();
}

function renderBoard() {
  const slots = $("#board-slots");
  const cards = rt.view?.board || [];
  const draft = rt.pendingOp?.op === "board" ? rt.pendingOp.cards : rt.gridMode === "board" ? rt.boardPicks : [];
  const html = [];
  for (let i = 0; i < 5; i++) {
    const c = cards[i] || draft[i - cards.length];
    html.push(`<span class="slot static ${c ? "filled " + (IS_RED[c[1]] ? "red" : "black") : ""}${c && i >= cards.length ? " pending" : ""}"
      aria-label="公共牌${i + 1}${c ? " " + c : " 未发"}">${c ? `<span class="r">${c[0]}</span><span class="s">${SUIT_GLYPH[c[1]]}</span>` : "+"}</span>`);
  }
  slots.innerHTML = html.join("");
  const hero = rt.session.currentHand?.heroCards?.filter(Boolean) || [];
  const picks = hero.length === 2 ? hero : rt.heroPicks;
  $("#hero-cards").innerHTML = [0, 1].map(i => cardPreview(picks[i])).join("");
  $("#street-caption").textContent = rt.gridMode === "board" || rt.pendingOp?.op === "board"
    ? `公共牌 ${draft.length} / ${nextBoardNeed()}${rt.pendingOp ? " · 更新中" : ""}`
    : STREET_LABEL[rt.view?.street] || "待选底牌";
}

function cardPreview(card) {
  return card ? `<span class="hero-card ${IS_RED[card[1]] ? "red" : "black"}" aria-label="${card}">${card[0]}${SUIT_GLYPH[card[1]]}</span>`
    : '<span class="hero-card vacant" aria-label="未选择">?</span>';
}

function renderOpsLine() {
  const s = rt.session;
  const el = $("#ops-line");
  const ops = s.currentHand?.ops || [];
  const viewSeats = rt.view ? new Map(rt.view.seats.map((x) => [x.seat_id, x])) : null;
  el.innerHTML = ops.filter((op) => op.op === "action").map((op, i) => {
    const label = viewSeats ? (viewSeats.get(op.seat_id)?.range_position || `${op.seat_id}号座`) : `${op.seat_id}号座`;
    const desc = op.type === "fold" ? "弃牌" : op.type === "allin" ? "全下"
      : op.type === "raise" ? `加注到 ${op.to}` : op.type === "check" ? "过牌"
      : `跟注 ${op.amount || ""}`;
    return `<span class="op-chip">${i + 1}. ${label} ${desc}</span>`;
  }).join("");
}

/* --------------------------------------------------------------- 牌库 */

function renderDeck() {
  const s = rt.session;
  const grid = $("#card-grid");
  const taken = new Set(rt.view?.taken || []);
  const heroCards = s?.currentHand?.heroCards || [];
  const heroDone = !!heroCards[0] && !!heroCards[1];
  const boardMode = rt.gridMode === "board" || rt.pendingOp?.op === "board";
  const picks = boardMode ? (rt.pendingOp?.cards || rt.boardPicks) : heroDone ? heroCards : rt.heroPicks;
  const needed = boardMode ? nextBoardNeed() : 2;
  $("#selection-label").textContent = boardMode ? "本次公共牌" : "我的底牌";
  $("#selection-cards").innerHTML = Array.from({length: needed}, (_, i) => cardPreview(picks[i])).join("");
  $("#selection-count").textContent = `${picks.length} / ${needed}`;
  const deckTip = $("#deck-tip");
  if (!s) {
    deckTip.textContent = "等待建桌";
  } else if (rt.coord?.viewPending()) {
    deckTip.textContent = "已选好 · 正在更新局面";
  } else if (boardMode) {
    deckTip.textContent = `公共牌 · 已选 ${picks.length}/${needed}`;
  } else if (!heroDone) {
    deckTip.textContent = `底牌 · 已选 ${picks.length}/2`;
  } else {
    deckTip.textContent = "底牌已确认 · 等待公共牌阶段";
  }
  // Keep card buttons mounted so repeated clicks and keyboard focus stay stable.
  if (!grid.children.length) {
    grid.innerHTML = SUITS.flatMap(suit => RANKS.map(rank => `<button type="button" class="grid-card ${IS_RED[suit] ? "red" : "black"}"
      data-card="${rank + suit}" aria-label="选择 ${rank + suit}">${rank}${SUIT_GLYPH[suit]}</button>`)).join("");
    grid.addEventListener("click", e => {
      const button = e.target.closest("[data-card]");
      if (button && !button.disabled) onDeckPick(button.dataset.card);
    });
  }
  for (const btn of grid.children) {
    const picked = picks.includes(btn.dataset.card);
    btn.classList.toggle("picked", picked);
    btn.setAttribute("aria-pressed", String(picked));
    btn.disabled = !s || rt.conflict || ["settling", "ended"].includes(s.phase) || !!rt.coord?.viewPending()
      || (!picked && taken.has(btn.dataset.card)) || (!boardMode && heroDone);
  }
  renderWorkflow();
}

function nextBoardNeed() {
  return rt.view?.board_dealing_count || 0;
}

function onDeckPick(card) {
  if (!rt.session || rt.conflict || rt.committing || rt.coord?.viewPending()) return;
  if (rt.gridMode === "board") {
    if (rt.boardPicks.includes(card)) {
      rt.boardPicks = rt.boardPicks.filter((x) => x !== card);
    } else if (rt.boardPicks.length < nextBoardNeed()) {
      rt.boardPicks.push(card);
    }
    if (rt.boardPicks.length === nextBoardNeed()) {
      const cards = [...rt.boardPicks];
      rt.boardPicks = [];
      rt.gridMode = null;
      submitAction({ op: "board", cards });
      return;
    }
    renderDeck();
    renderBoard();
    return;
  }
  const hand = rt.session.currentHand;
  if (hand.heroCards[0] && hand.heroCards[1]) return;
  rt.heroPicks = rt.heroPicks.includes(card) ? rt.heroPicks.filter(c => c !== card) : [...rt.heroPicks, card].slice(-2);
  if (rt.heroPicks.length === 2) {
    setHeroCards(rt.session, rt.heroPicks);
    rt.heroPicks = [];
    persist();
    renderAll();
    refreshView();
    // 5.4：手机端选齐底牌后收起牌库
    if (window.matchMedia("(max-width: 767px)").matches) {
      $("#deck-panel").open = false;
    }
    return;
  }
  renderDeck();
  renderBoard();
  renderConsole();
}

/* --------------------------------------------------------------- 操作台 */

function setStatus(msg) { $("#status-line").textContent = msg; }

function renderConsole() {
  renderWorkflow();
  const s = rt.session;
  if (!s) return;
  const area = $("#action-area");
  if (rt.error) {
    setStatus(rt.error);
    area.innerHTML = '<button type="button" class="ghost-btn" id="retry-view">重试更新</button>';
    $("#retry-view").onclick = refreshView; return;
  }
  const hand = s.currentHand;
  if (s.phase === "ended") {
    setStatus("本桌已结束。可在上方新建牌桌。");
    area.innerHTML = "";
    return;
  }
  if (s.phase === "settling") {
    setStatus("请核对全桌手后实际余额，确认后进入下一手。");
    area.innerHTML = "";
    return;
  }
  if (!hand) return;
  if (!hand.heroCards[0] || !hand.heroCards[1]) {
    setStatus(`底牌已选 ${rt.heroPicks.length}/2${rt.heroPicks.length ? " · 还差 1 张" : ""}`);
    area.innerHTML = '<button type="button" class="ghost-btn" id="manual-close-empty">本手未完整记录，核对余额</button>';
    $("#manual-close-empty").onclick = onManualClose;
    return;
  }
  if (!rt.view) {
    setStatus(rt.coord?.viewPending() ? "正在更新局面…" : "等待局面更新…");
    area.innerHTML = "";
    return;
  }
  const heroSeat = heroSeatOf(s);
  if (rt.view.hand_over) {
    setStatus("本手牌谱已结束，可撤销最后一步纠正记录。");
    area.innerHTML = '<button type="button" class="ghost-btn" data-act="undo">撤销上一步</button><button type="button" class="ghost-btn" id="return-settlement">返回核对余额</button>';
    area.querySelector('[data-act="undo"]').onclick = onUndo;
    area.querySelector('#return-settlement').onclick = () => enterSettlePhase(rt.view);
    return;
  }
  if (rt.view.actor_seat_id == null) {
    setStatus("等待录入公共牌"); renderActionButtons(false); return;
  }
  const heroInfo = rt.view.seats.find((x) => x.seat_id === heroSeat);
  if (rt.view.actor_seat_id !== heroSeat) {
    setStatus(heroInfo?.folded
      ? `你已弃牌。当前轮到 ${rt.view.actor_seat_id}号座，可继续录入其余玩家动作。`
      : `轮到 ${rt.view.actor_seat_id}号座（${rt.view.seats.find((x) => x.seat_id === rt.view.actor_seat_id)?.range_position || ""}）行动，请录入其动作。`);
    renderActionButtons(false);
    return;
  }
  const toCall = rt.view.to_call || 0;
  setStatus(rt.coord?.viewPending()
    ? "正在更新局面…"
    : `轮到你（${heroSeat}号座）行动 · 需跟注 ${actionAmount(toCall)}`);
  renderActionButtons(true);
}

function renderActionButtons(isHero) {
  const area = $("#action-area");
  const s = rt.session;
  const view = rt.view;
  if (!view) { area.innerHTML = ""; return; }
  const pending = rt.coord.viewPending();
  const dis = pending ? "disabled" : "";
  const btn = (action, label, cls = "") =>
    `<button type="button" class="act-btn ${cls}" data-act="${action}" ${dis}>${label}</button>`;
  const seat = view.actor_seat_id;
  const toCall = view.to_call || 0;
  let html = seat == null ? "" : (view.can_fold ? btn(`fold:${seat}`, "弃牌") : "")
    + btn(`call:${seat}`, toCall > 0 ? `跟注 ${actionAmount(toCall)}` : "过牌");
  const minTo = view.min_raise_to;
  const maxTo = view.max_raise_to;
  if (minTo != null && maxTo != null && minTo <= maxTo) {
    const unit = rt.unit === "bb" ? "BB" : "筹码";
    const value = rt.unit === "bb" ? bbInputValue(minTo) : String(minTo);
    const maxValue = rt.unit === "bb" ? bbInputValue(maxTo) : String(maxTo);
    html += btn(`allin:${seat}`, "全下", "allin")
      + `<div class="raise-box"><label for="raise-input">加注到（本街总额）</label>
          <div class="raise-entry"><input type="number" id="raise-input" inputmode="${rt.unit === "bb" ? "decimal" : "numeric"}"
            value="${value}" min="${value}" max="${maxValue}" step="${rt.unit === "bb" ? "any" : "1"}"
            data-initial-value="${value}" data-initial-chips="${minTo}" ${dis}
            aria-describedby="raise-range raise-conversion raise-error"><span>${unit}</span>
          <button type="button" class="act-btn" data-act="raise:${seat}" ${dis}>加注</button></div>
          <div id="raise-range" class="tip">可加注到 ${value}～${maxValue} ${unit}</div>
          <div id="raise-conversion" class="tip" aria-live="polite"></div>
          <div id="raise-error" class="form-error" role="alert"></div></div>`;
  }
  const need = nextBoardNeed();
  if (need) html += `<button type="button" class="ghost-btn" data-act="deal-board" ${pending || rt.gridMode === "board" ? "disabled" : ""}>发${STREET_LABEL[view.street] || ""}（${need} 张）</button>`;
  if (rt.gridMode === "board") html += '<button type="button" class="ghost-btn" data-act="cancel-board">取消选牌</button>';
  html += `<button type="button" class="ghost-btn" data-act="undo" ${pending || !s.currentHand.ops.length ? "disabled" : ""}>撤销上一步</button>`;
  html += `<button type="button" class="ghost-btn" data-act="manual-close" ${dis}>结束本手，手动核对余额</button>`;
  area.innerHTML = html;
  for (const el of area.querySelectorAll("[data-act]")) {
    el.addEventListener("click", () => onActButton(el.dataset.act));
  }
  area.querySelector("#raise-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); onActButton(`raise:${view.actor_seat_id}`); }
  });
  area.querySelector("#raise-input")?.addEventListener("input", updateRaisePreview);
  updateRaisePreview();
}

function raiseInputChips(input, confirmRound = false) {
  // Displaying a repeating BB fraction must not change the exact engine minimum.
  if (input.value === input.dataset.initialValue) return Number(input.dataset.initialChips);
  return amountInput(input.value, rt.unit, rt.session.blindLevel.bb, confirmRound);
}

function updateRaisePreview() {
  const input = $("#raise-input");
  if (!input) return;
  $("#raise-error").textContent = "";
  input.removeAttribute("aria-invalid");
  try {
    const to = raiseInputChips(input);
    const bet = rt.view.seats.find(seat => seat.seat_id === rt.view.actor_seat_id)?.bet || 0;
    const conversion = rt.unit === "bb" ? chipsFromBB(input.value, rt.session.blindLevel.bb) : null;
    const rounding = conversion?.changed && input.value !== input.dataset.initialValue ? "（需取整确认）" : "";
    $("#raise-conversion").textContent = `总额 ${fmtChips(to)} 筹码${rounding} · 本次再投入 ${fmtChips(Math.max(0, to - bet))} 筹码`;
  } catch {
    $("#raise-conversion").textContent = "";
  }
}

function onActButton(spec) {
  const view = rt.view;
  if (!view || rt.conflict || rt.committing || rt.coord.viewPending()) return;
  const [action, seatStr] = spec.split(":");
  const seatId = Number(seatStr) || view.actor_seat_id;
  if (action === "fold") return submitAction({ op: "action", seat_id: seatId, type: "fold" });
  if (action === "call") return submitAction({ op: "action", seat_id: seatId, type: view.to_call > 0 ? "call" : "check" });
  if (action === "allin") return submitAction({ op: "action", seat_id: seatId, type: "allin" });
  if (action === "raise") {
    const input = $("#raise-input");
    if (!input) return;
    try {
      const to = raiseInputChips(input);
      if (view.min_raise_to == null || view.max_raise_to == null || to < view.min_raise_to || to > view.max_raise_to) {
        throw new Error(`加注总额须在 ${actionAmount(view.min_raise_to)} 至 ${actionAmount(view.max_raise_to)} 之间`);
      }
      raiseInputChips(input, true);
      return submitAction({ op: "action", seat_id: seatId, type: "raise", to });
    } catch (e) {
      $("#raise-error").textContent = e.message;
      input.setAttribute("aria-invalid", "true");
      input.focus({preventScroll: true});
      return;
    }
  }
  if (action === "undo") return onUndo();
  if (action === "manual-close") return onManualClose();
  if (action === "cancel-board") {
    rt.gridMode = null; rt.boardPicks = []; renderDeck(); renderConsole();
    renderBoard();
    areaFocus(); return;
  }
  if (action === "deal-board") {
    rt.gridMode = "board";
    rt.boardPicks = [];
    $("#deck-panel").open = true;
    renderDeck();
    renderBoard();
    renderConsole();
    $("#deck-panel").scrollIntoView({ behavior: "instant", block: "nearest" });
  }
}

function areaFocus() {
  // Touch click processing may clear focus when its original button is removed.
  requestAnimationFrame(() => $('[data-act="deal-board"]')?.focus({preventScroll: true}));
}

function renderAdvice() {
  renderWorkflow();
  const s = rt.session;
  const box = $("#advice");
  const note = $("#advice-note");
  if (!s || s.phase !== "playing" || !rt.view || rt.view.hand_over
    || rt.view.actor_seat_id !== heroSeatOf(s)) {
    const stateText = {
      idle: "轮到你行动时，这里给出与最新录入局面匹配的推演。",
      pending: "正在推演…（不影响继续录入动作）",
      failed: "推演失败。",
      updated: "等待轮到你行动",
    }[rt.adviceState];
    box.innerHTML = `<p class="empty">${stateText}</p>${rt.adviceState === "failed"
      ? '<button type="button" class="ghost-btn" data-action="retry-advice">重试推演</button>' : ""}`;
    box.querySelector("[data-action=retry-advice]")?.addEventListener("click", () => maybeAdvice());
    note.textContent = "";
    return;
  }
  if (rt.adviceState === "failed") {
    box.innerHTML = '<p class="form-error">推演失败</p><button type="button" id="retry-advice">重试推演</button>';
    $("#retry-advice").onclick = maybeAdvice; note.textContent = ""; return;
  }
  if (rt.adviceState === "pending" || !rt.advice) {
    box.innerHTML = '<p class="empty">正在推演…</p>';
    note.textContent = "";
    return;
  }
  const adv = rt.advice.advice;
  const bb = s.blindLevel.bb;
  const eq = adv.equity || {};
  const rec = adv.recommendation || {};
  const mixHtml = (mix) => (mix || []).map((m) => `
    <div class="mix-row"><span class="mix-label">${esc(m.label)}</span>
      <span class="mix-bar"><i style="width:${Math.max(2, Math.min(100, m.pct))}%"></i></span>
      <span class="mix-pct">${m.pct}%</span></div>`).join("");
  let cfrHtml = "";
  const c = adv.cfr;
  if (c && c.supported) {
    cfrHtml = `<div class="cfr-box"><div class="cfr-title">CFR 均衡参考
        <span class="cfr-meta">${c.street || "河牌"}${c.approximate ? "（近似）" : ""} · ${c.iterations} 次迭代</span></div>
      ${mixHtml(c.mix)}
      <p class="cfr-note">对范围权益约 ${c.equity_vs_range}%。${c.agree ? "与启发式方向一致。" : "与启发式方向不同：均衡频率，供交叉参考。"}</p></div>`;
  }
  const oppRows = (adv.opponents || []).map((o) =>
    `<tr><td>${esc(o.pos || "")}</td><td>${esc(o.situation || "")} · ${o.combos || ""} 组合</td>
     <td>${chipsToBBText(o.stack || 0, bb)}BB</td></tr>`).join("");
  box.innerHTML = `
    <div class="big">${eq.win.toFixed(1)}<small>% 胜率（含平 ${eq.tie.toFixed(1)}%，范围估算）</small></div>
    <div class="bar" role="img" aria-label="胜 ${eq.win}% 平 ${eq.tie}% 负 ${eq.lose}%">
      <i class="w" style="width:${eq.win}%"></i><i class="t" style="width:${eq.tie}%"></i><i class="l" style="width:${eq.lose}%"></i></div>
    <div class="rec-box"><div class="rec-title">行动建议（启发式）</div>
      <div class="rec-primary">${esc(rec.primary || "—")}</div>
      ${mixHtml(rec.mix)}
      <p class="rec-reason">${esc(rec.reason || "")}</p></div>
    <table class="adv-table">
      <tr><td>需跟注</td><td>${adv.to_call > 0 ? chipsToBBText(adv.to_call, bb) + " BB" : "0（可过牌）"}</td></tr>
      <tr><td>跟注所需胜率</td><td>${adv.required_eq}%</td></tr>
      <tr><td>你的权益</td><td>${(adv.equity_share ?? eq.win + eq.tie / 2).toFixed(1)}%（可争夺份额）</td></tr>
      <tr><td>跟注 EV</td><td class="${adv.ev_call >= 0 ? "pos" : "neg"}">${adv.ev_call >= 0 ? "+" : ""}${(adv.ev_call / bb).toFixed(1)} BB</td></tr>
      <tr><td>SPR</td><td>${rec.spr ?? "—"}${rec.spr_note ? " · " + esc(rec.spr_note) : ""}</td></tr>
      <tr><td>M 值</td><td>${adv.m_value ?? "—"}</td></tr>
    </table>
    <details><summary class="ghost-btn">详细范围与对手</summary>
      <table class="adv-table"><thead><tr><th>位置</th><th>局面 · 组合</th><th>剩余</th></tr></thead><tbody>${oppRows}</tbody></table>
    </details>
    ${cfrHtml}
    <p class="footnote">${esc(adv.note || "")} · 基于 ${fmtChips(eq.iterations)} 次模拟（范围假设，非实测）</p>`;
  note.textContent = `需跟注 ${fmtChips(adv.to_call || 0)} / ${chipsToBBText(adv.to_call || 0, bb)}BB`;
}

function renderHistory() {
  const body = $("#history-body");
  const s = rt.session;
  if (!s) { body.innerHTML = ""; return; }
  $("#history-tip").textContent = `最近 ${s.recentHands.length} 手（窗口 100）`;
  body.innerHTML = s.recentHands.slice().reverse().map((h) =>
    `<p class="footnote">第 ${h.handNumber} 手 · ${h.recordQuality === "manual_close" ? "手工结束" : "完整"} ·
      ${new Date(h.settledAt).toLocaleString("zh-CN")}${h.adjustmentReason ? ` · 差额原因：${esc(h.adjustmentReason)}` : ""}</p>`).join("")
    || '<p class="footnote">尚无已完成的手。</p>';
}

function renderAdvanced() {
  const s = rt.session;
  if (!s) return;
  const sw = $("#tg-learning-switch");
  sw.checked = s.nextHandDraft?.learningEnabled ?? s.learningEnabled;
  if (!sw.dataset.bound) {
    sw.dataset.bound = "1";
    sw.addEventListener("change", () => {
      if (!rt.session.nextHandDraft) {
        rt.session.nextHandDraft = emptyNextHandDraft(rt.session);
      }
      rt.session.nextHandDraft.learningEnabled = sw.checked;
      persist();
      renderProfileNames();
      if (rt.session.phase === "settling") renderSettle();
    });
  }
  let unitSel = $("#tg-unit-live");
  if (!unitSel) {
    const wrap = document.createElement("div");
    wrap.className = "setup-row";
    wrap.innerHTML = `<label>显示 / 加注单位
      <select id="tg-unit-live"><option value="bb">BB 优先</option>
      <option value="chips">筹码优先</option></select></label>
      <button type="button" class="ghost-btn" id="tg-export">导出会话</button>
      <button type="button" class="ghost-btn" id="tg-end-session">结束本桌</button>`;
    $("#advanced-body").prepend(wrap);
    unitSel = wrap.querySelector("#tg-unit-live");
    unitSel.value = rt.unit;
    unitSel.addEventListener("change", (e) => {
      const oldInput = $("#raise-input");
      let draft;
      try { if (oldInput) draft = raiseInputChips(oldInput, true); }
      catch (error) {
        if (error.code === "cancelled") { unitSel.value = rt.unit; return; }
      }
      rt.unit = e.target.value;
      rt.session.displayUnit = rt.unit;
      persist();
      renderAll();
      const input = $("#raise-input");
      if (input && draft !== undefined) {
        input.value = rt.unit === "bb" ? bbInputValue(draft) : String(draft);
        input.dataset.initialValue = input.value;
        input.dataset.initialChips = String(draft);
        updateRaisePreview();
      }
    });
    wrap.querySelector("#tg-export").addEventListener("click", () => {
      downloadRaw(JSON.stringify(rt.session, null, 2));
    });
    wrap.querySelector("#tg-end-session").addEventListener("click", () => {
      if (!window.confirm("结束当前牌桌？已确认的余额与本桌记录将保留。")) return;
      import("./session.js").then((S) => {
        S.endSession(rt.session);
        rt.coord.resync(rt.session);
        persist();
        renderAll();
      });
    });
  }
  renderProfileNames();
}

/* --------------------------------------------------------------- 绑定 */

function renderProfileNames() {
  let area = $("#profile-names");
  if (!area) {
    area = document.createElement("div"); area.id = "profile-names";
    $("#advanced-body").appendChild(area);
  }
  const s = rt.session;
  area.hidden = !(s.nextHandDraft?.learningEnabled ?? s.learningEnabled);
  if (area.hidden) { area.innerHTML = ""; return; }
  area.innerHTML = s.seats.filter(seat => s.occupants[seat.occupantId]?.status === "active")
    .map(seat => `<label class="setup-row">${seat.id}号座代号 <input type="text" data-profile="${esc(seat.occupantId)}"
      value="${esc(s.nextHandDraft?.profileNames?.[seat.occupantId] ?? s.occupants[seat.occupantId].profileName ?? "")}"></label>`).join("");
  area.querySelectorAll("input").forEach(input => input.oninput = () => {
    s.nextHandDraft ??= emptyNextHandDraft(s); s.nextHandDraft.profileNames ??= {};
    s.nextHandDraft.profileNames[input.dataset.profile] = input.value; persist();
  });
}

function renderNextSettings() {
  const s = rt.session;
  if (!s || s.phase !== "settling") return;
  const draft = s.nextHandDraft ??= emptyNextHandDraft(s);
  const b = draft.blindLevel;
  const body = $("#nextround-body");
  body.innerHTML = `<div class="setup-row">
    <label>下手小盲 <input id="next-sb" type="number" value="${b.sb}" min="1"></label>
    <label>下手大盲 <input id="next-bb" type="number" value="${b.bb}" min="1"></label>
    <label>每人前注 <input id="next-ante" type="number" value="${b.anteEach}" min="0"></label>
  </div><label class="switch-line"><input type="checkbox" id="next-icm" ${draft.icm.scope === "final_table" ? "checked" : ""}> 决赛桌 ICM（本桌包含全部剩余选手）</label>
  <label id="next-payouts-label" ${draft.icm.scope === "final_table" ? "" : "hidden"}>剩余名次奖金
    <input id="next-payouts" value="${esc(draft.icm.payouts.join(","))}" placeholder="500,300,200">
  </label><p class="form-error" id="next-error">${esc(draft.settingsError || "")}</p>`;
  const save = () => {
    try {
      setDraftBlinds(draft, $("#next-sb").value, $("#next-bb").value, $("#next-ante").value);
      if ($("#next-icm").checked && !$("#next-payouts").value.trim()) throw new Error("请填写剩余名次奖金");
      draft.icm = $("#next-icm").checked ? parsePayouts($("#next-payouts").value) : {scope: "off", payouts: [], rosterConfirmed: false};
      draft.settingsError = "";
    } catch (e) { draft.settingsError = e.message; }
    $("#next-error").textContent = draft.settingsError;
    $("#next-payouts-label").hidden = !$("#next-icm").checked;
    persist();
  };
  body.querySelectorAll("input").forEach(el => el.addEventListener("input", save));
}

function renderIcm() {
  const panel = $("#icm-panel"), icm = rt.view?.icm;
  panel.hidden = !icm;
  if (!icm) return;
  panel.innerHTML = `<details><summary>决赛桌 ICM · 手前筹码快照</summary>
    <table class="adv-table"><thead><tr><th>座位</th><th>奖金期望</th><th>份额</th></tr></thead>
    <tbody>${icm.rows.map(row => `<tr><td>${row.seat_id}号座${row.hero ? " · 你" : ""}</td><td>${row.equity}</td><td>${row.pct}%</td></tr>`).join("")}</tbody></table></details>`;
}

function bindGlobal() {
  $("#tg-capacity").addEventListener("change", renderSetupSeats);
  $("#tg-unit").addEventListener("change", () => {
    try {
      const bb = Number($("#tg-bb").value), input = $("#tg-default-chips");
      const chips = amountInput(input.value, rt.setupUnit, bb);
      input.value = $("#tg-unit").value === "chips" ? chips : chips / bb;
      rt.setupUnit = $("#tg-unit").value;
      updateSetupAmountHint();
    } catch (e) { $("#tg-unit").value = rt.setupUnit; setupError(e.message); }
  });
  $("#tg-learning").addEventListener("change", renderSetupSeats);
  for (const id of ["tg-default-chips", "tg-bb"]) $("#" + id).addEventListener("input", updateSetupAmountHint);
  $("#tg-create").addEventListener("click", onPreviewTable);
  $("#tg-confirm").addEventListener("click", onConfirmTable);
  for (const event of ["click", "input", "change", "keydown"]) document.addEventListener(event, e => {
    if (e.target.closest("#conflict-banner")) return;
    if (rt.conflict || rt.committing) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  Store.onStorageChange(newValue => { if (newValue !== rt.expectedRaw) showConflict(); });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
