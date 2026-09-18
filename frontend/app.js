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
  pendingLearningJobs, markLearningJob,
} from "./session.js";
import * as Store from "./session-storage.js";
import { createCoordinator } from "./request-coordinator.js";

/* --------------------------------------------------------------- 基础工具 */

const $ = (sel) => document.querySelector(sel);
const RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"];
const SUITS = ["s", "h", "d", "c"];
const SUIT_GLYPH = { s: "♠", h: "♥", d: "♦", c: "♣" };
const IS_RED = { s: false, h: true, d: true, c: false };
const STREET_DEAL = { preflop: 3, flop: 1, turn: 1 };
const STREET_LABEL = { preflop: "翻牌前", flop: "翻牌", turn: "转牌", river: "河牌" };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtChips(n) { return Number(n).toLocaleString("zh-CN"); }
function fmtNum(n) { return (Math.round((Number(n) || 0) * 10) / 10).toFixed(1); }

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
  saveState: "",
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
  } else {
    renderAll();
  }
}

function showSetupWizard(loaded) {
  $("#layout").dataset.phase = "setup";
  $("#col-table").hidden = true;
  $("#console").hidden = true;
  $("#advice-panel").hidden = true;
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
    $("#tg-sb").value = legacy.sb;
    $("#tg-bb").value = legacy.bb;
    $("#tg-ante").value = legacy.anteEach;
    $("#tg-default-chips").value = legacy.defaultChips;
  }
  renderSetupSeats();
}

function downloadRaw(text) {
  const blob = new Blob([text], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "paishi-session-backup.json";
  a.click();
}

/* ------------------------------------------------------------ 建桌向导 */

function renderSetupSeats() {
  const capacity = Number($("#tg-capacity").value) || 6;
  const wrap = $("#tg-seats");
  const next = rt.setup.occupied;
  for (const k of [...next.keys()]) if (k > capacity) next.delete(k);
  const rows = [];
  for (let sid = 1; sid <= capacity; sid++) {
    const occ = next.get(sid);
    rows.push(`<div class="tg-seat-row${occ ? " occupied" : ""}" data-seat="${sid}">
      <label class="tg-occ"><input type="checkbox" data-role="occ" ${occ ? "checked" : ""}> ${sid}号座</label>
      <input type="text" data-role="name" class="tg-name" placeholder="代号（可选）" value="${esc(occ?.name || "")}" ${occ ? "" : "disabled"}>
      <input type="number" data-role="chips" class="tg-chips" min="1" placeholder="筹码" value="${occ ? esc(occ.chips ?? "") : ""}" ${occ ? "" : "disabled"}>
      <span class="tg-seat-tags">
        <button type="button" class="tag-btn" data-role="hero" title="我的座位">你</button>
        <button type="button" class="tag-btn" data-role="btn" title="庄位">BTN</button>
        <button type="button" class="tag-btn" data-role="sb" title="小盲">SB</button>
        <button type="button" class="tag-btn" data-role="bb" title="大盲">BB</button>
      </span>
    </div>`);
  }
  wrap.innerHTML = rows.join("");
  for (const row of wrap.querySelectorAll(".tg-seat-row")) {
    const sid = Number(row.dataset.seat);
    row.querySelector('[data-role="occ"]').addEventListener("change", (e) => {
      const chipsInput = row.querySelector('[data-role="chips"]');
      const nameInput = row.querySelector('[data-role="name"]');
      if (e.target.checked) {
        const def = Number($("#tg-default-chips").value) || 10000;
        chipsInput.value = String(def);
        next.set(sid, { chips: def, name: "" });
        chipsInput.disabled = false; nameInput.disabled = false;
      } else {
        next.delete(sid);
        chipsInput.disabled = true; nameInput.disabled = true;
        for (const key of ["heroSeatId", "buttonSeatId", "sbSeatId", "bbSeatId"]) {
          if (rt.setup[key] === sid) rt.setup[key] = null;
        }
      }
      markSeatTags(row, sid);
    });
    row.querySelector('[data-role="chips"]').addEventListener("input", (e) => {
      const cur = next.get(sid);
      if (cur) cur.chips = e.target.value === "" ? null : Number(e.target.value);
    });
    row.querySelector('[data-role="name"]').addEventListener("input", (e) => {
      const cur = next.get(sid);
      if (cur) cur.name = e.target.value;
    });
    const KEY_BY_ROLE = { hero: "heroSeatId", btn: "buttonSeatId", sb: "sbSeatId", bb: "bbSeatId" };
    for (const role of ["hero", "btn", "sb", "bb"]) {
      row.querySelector(`[data-role="${role}"]`).addEventListener("click", () => {
        if (!next.has(sid)) return;
        const key = KEY_BY_ROLE[role];
        rt.setup[key] = rt.setup[key] === sid ? null : sid;
        wrap.querySelectorAll(".tg-seat-row").forEach((r) => markSeatTags(r, Number(r.dataset.seat)));
      });
    }
  }
  wrap.querySelectorAll(".tg-seat-row").forEach((r) => markSeatTags(r, Number(r.dataset.seat)));
}

function markSeatTags(row, sid) {
  row.querySelector('[data-role="hero"]').classList.toggle("picked", rt.setup.heroSeatId === sid);
  row.querySelector('[data-role="btn"]').classList.toggle("picked", rt.setup.buttonSeatId === sid);
  row.querySelector('[data-role="sb"]').classList.toggle("picked", rt.setup.sbSeatId === sid);
  row.querySelector('[data-role="bb"]').classList.toggle("picked", rt.setup.bbSeatId === sid);
  row.classList.toggle("occupied", rt.setup.occupied.has(sid));
}

function setupError(msg) {
  const el = $("#setup-error");
  el.textContent = msg || "";
  el.hidden = !msg;
}

function collectSetup() {
  const bb = Number($("#tg-bb").value);
  const entries = [];
  for (const [sid, occ] of rt.setup.occupied) {
    if (occ.chips == null || !Number.isSafeInteger(occ.chips) || occ.chips <= 0) {
      throw new DomainError("bad_chips", `${sid}号座筹码无效`);
    }
    entries.push({ seatId: sid, chips: occ.chips, name: occ.name || null });
  }
  return {
    capacity: Number($("#tg-capacity").value),
    entries,
    heroSeatId: rt.setup.heroSeatId,
    buttonSeatId: rt.setup.buttonSeatId,
    sbSeatId: rt.setup.sbSeatId,
    bbSeatId: rt.setup.bbSeatId,
    blindLevel: { sb: Number($("#tg-sb").value), bb, anteEach: Number($("#tg-ante").value) || 0 },
    learningEnabled: $("#tg-learning").checked,
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

function onPreviewTable() {
  setupError("");
  let session;
  try {
    session = createSession(collectSetup());
  } catch (e) {
    setupError(e.message);
    return;
  }
  rt.setupPreview = session;
  const preview = $("#tg-preview");
  preview.hidden = false;
  const rows = session.seats.filter((x) => x.occupantId).map((x) => {
    const occ = session.occupants[x.occupantId];
    return `<tr><td>${x.id}号座</td><td>${esc(occ.profileName || "—")}${x.occupantId === session.heroOccupantId ? " · 你" : ""}</td>
      <td>${chipsToBBText(occ.confirmedChips, session.blindLevel.bb)}BB / ${fmtChips(occ.confirmedChips)}</td>
      <td>${esc(rolesForSeat(session, x.id).join("/") || rangePositionFor(session, x.id) || "")}</td></tr>`;
  });
  const p = session.positions;
  preview.innerHTML = `<table class="adv-table"><thead><tr><th>座位</th><th>玩家</th><th>筹码</th><th>角色</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table>
    <p class="footnote">BTN ${p.buttonSeatId}号座 · SB ${p.smallBlindSeatId ?? "空"} · BB ${p.bigBlindSeatId}号座。
    确认后从第 1 手开始；熟人记牌默认关闭，可在“更多设置”按手调整。</p>`;
  $("#tg-confirm").hidden = false;
  $("#tg-create").textContent = "重新生成预览";
}

async function onConfirmTable() {
  setupError("");
  const btn = $("#tg-confirm");
  btn.disabled = true;
  try {
    const session = rt.setupPreview || createSession(collectSetup());
    await prepareContext(session, session.currentHand.handId, 1);   // A03：后端验证后才开始
    rt.session = session;
    rt.coord = createCoordinator(session, playerHeaders);
    rt.unit = "bb";
    persist();
    renderAll();
  } catch (e) {
    setupError(`建桌失败：${e.message}`);
  } finally {
    btn.disabled = false;
  }
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
  if (!rt.session || learningSyncing) return;
  const jobs = pendingLearningJobs(rt.session);
  renderLearningStatus();
  if (!jobs.length || !rt.session.learningEnabled) return;
  learningSyncing = true;
  for (const job of jobs) {
    try {
      const res = await fetch("/api/hand/v2/record", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...playerHeaders() },
        body: JSON.stringify(job.payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) continue;      // 学习已关闭：暂停，保留任务
      if (res.ok && data.recorded) {
        markLearningJob(rt.session, job.handId, "success");
      } else {
        markLearningJob(rt.session, job.handId, "failed",
          data.detail || `HTTP ${res.status}`);
      }
    } catch (e) {
      markLearningJob(rt.session, job.handId, "failed", e.message);
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

function persist() {
  if (!rt.session) return;
  const r = Store.saveSession(rt.session);
  rt.saveState = r.ok ? "saved" : "error";
  setSaveLabel();
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
    iterations: 20000,
  };
}

function refreshView() {
  rt.coord.resync(rt.session);
  rt.coord.submitView(rt.session, viewPayload(rt.session.currentHand.ops), {
    onAccept(body) {
      rt.view = body;
      rt.advice = null;
      rt.adviceState = "idle";
      if (rt.session.phase === "ready") rt.session.phase = "playing";
      if (body.hand_over && rt.session.phase === "playing") enterSettlePhase(body);
      renderAll();
      maybeAdvice();
    },
    onError(err) {
      setStatus(`局面更新失败：${err.message}`);
      renderConsole();
    },
  });
  renderAll();
}

function submitAction(op) {
  if (rt.coord.viewPending()) return;          // S04.1：不排队第二个未验证动作
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
    },
    onError(err) {
      rt.pendingOp = null;                     // 待确认动作从未落地（S-13）
      setStatus(`动作未被接受：${err.message}`);
      renderAll();
    },
  });
  renderAll();
}

function onUndo() {
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
  } catch (e) {
    setStatus(e.message);
    return;
  }
  persist();
  renderAll();
}

function enterSettlePhase(view) {
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
    const srcTag = row.source === "unknown" ? '<span class="src-tag warn">待核对</span>'
      : row.source === "manual" ? '<span class="src-tag">手工</span>'
      : '<span class="src-tag ok">已核对</span>';
    return `<tr data-seat="${row.seatId}">
      <td>${row.seatId}号座${isHero ? " · 你" : ""}</td>
      <td>${esc(occ?.profileName || "—")}</td>
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
      <thead><tr><th>座位</th><th>玩家</th><th>手前</th><th>来源</th><th>手后实际余额</th><th>BB</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="settle-tools">
      <button type="button" class="ghost-btn" data-action="confirm-current">确认当前填写值</button>
      ${deltaLine}
    </div>
    <div class="setup-row" id="settle-diff-row" ${v.delta === 0 ? "hidden" : ""}>
      <input type="text" id="settle-reason" placeholder="差额原因（现场漏记/初始录错等）" value="${esc(draft.adjustmentReason || "")}">
      <label class="switch-line"><input type="checkbox" id="settle-accept" ${draft.differenceAccepted ? "checked" : ""}> 我确认该差额</label>
    </div>
    <div id="roster-area"></div>
    <p class="form-error" id="settle-error" role="alert" hidden></p>
    <div class="settle-actions">
      <button type="button" class="primary-btn" id="settle-commit" data-action="settle-commit">确认余额，进入下一手</button>
      <button type="button" class="ghost-btn" data-action="settle-cancel">取消，继续纠正本手</button>
    </div>`;
  bindSettleEvents(area);
  renderRosterEditor(area.querySelector("#roster-area"));
}

function bindSettleEvents(area) {
  for (const input of area.querySelectorAll(".settle-input")) {
    input.addEventListener("change", (e) => {
      const seatId = Number(e.target.dataset.seat);
      const draft = rt.session.settlementDraft;
      if (e.target.value === "") { editSettlementRow(draft, seatId, null); }
      else {
        const n = Number(e.target.value);
        if (!Number.isSafeInteger(n) || n < 0) {
          showSettleError(`${seatId}号座余额必须为非负整数`);
          return;
        }
        editSettlementRow(draft, seatId, n, "manual");
      }
      persist();
      renderSettle();
    });
  }
  area.querySelector('[data-action="confirm-current"]')?.addEventListener("click", () => {
    confirmAllCurrentValues(rt.session.settlementDraft);
    persist();
    renderSettle();
  });
  area.querySelector("#settle-reason")?.addEventListener("input", (e) => {
    rt.session.settlementDraft.adjustmentReason = e.target.value;
  });
  area.querySelector("#settle-accept")?.addEventListener("change", (e) => {
    rt.session.settlementDraft.differenceAccepted = e.target.checked;
  });
  area.querySelector('[data-action="settle-cancel"]')?.addEventListener("click", () => {
    cancelSettlement(rt.session);
    rt.view = null;
    persist();
    refreshView();
  });
  area.querySelector("#settle-commit")?.addEventListener("click", () => commitSettlement(area));
}

function showSettleError(msg) {
  const el = $("#settle-error");
  if (el) { el.textContent = msg || ""; el.hidden = !msg; }
}

function renderRosterEditor(container) {
  const s = rt.session;
  const html = `
    <div class="group-title">下一手成员 <span class="tip">（离桌/入座/换座；先填好本手余额）</span></div>
    <div class="tg-seats" id="roster-rows">${s.seats.map((seat) => {
      const occ = seat.occupantId ? s.occupants[seat.occupantId] : null;
      if (!occ || occ.status !== "active") {
        return `<div class="tg-seat-row" data-seat="${seat.id}">
          <span class="inline-label">${seat.id}号座 · 空位</span>
          <input type="number" class="roster-enter-chips" data-seat="${seat.id}" placeholder="新玩家筹码" min="1">
          <input type="text" class="roster-enter-name" data-seat="${seat.id}" placeholder="代号（可选）">
          <button type="button" class="ghost-btn roster-enter" data-seat="${seat.id}">入座</button>
        </div>`;
      }
      const isHero = occ.id === s.heroOccupantId;
      return `<div class="tg-seat-row occupied" data-seat="${seat.id}">
        <span class="inline-label">${seat.id}号座 · ${esc(occ.profileName || "玩家")}${isHero ? " · 你" : ""} · ${fmtChips(occ.confirmedChips)}</span>
        <button type="button" class="ghost-btn roster-leave" data-occ="${occ.id}">离桌/淘汰</button>
        ${isHero ? "" : `<button type="button" class="ghost-btn roster-move" data-occ="${occ.id}">移到空座</button>`}
      </div>`;
    }).join("")}</div>
    <div id="position-preview"></div>`;
  container.innerHTML = html;
  for (const btn of container.querySelectorAll(".roster-enter")) {
    btn.addEventListener("click", () => {
      const seatId = Number(btn.dataset.seat);
      const chips = Number(container.querySelector(`.roster-enter-chips[data-seat="${seatId}"]`).value);
      const name = container.querySelector(`.roster-enter-name[data-seat="${seatId}"]`).value;
      try {
        applyRosterEdit(s, s.nextHandDraft, { type: "enter", seatId, chips, name });
      } catch (e) { showSettleError(e.message); return; }
      renderRosterEditor(container);
    });
  }
  for (const btn of container.querySelectorAll(".roster-leave")) {
    btn.addEventListener("click", () => {
      const occId = btn.dataset.occ;
      const zero = s.settlementDraft.rows.find((r) => r.occupantId === occId)?.finalChips === 0;
      const reason = window.prompt(zero ? "该玩家最终余额为 0，确认按淘汰处理？" : "离桌原因：table_transfer（转桌）/ eliminated（淘汰）", zero ? "eliminated" : "table_transfer");
      if (!reason) return;
      try {
        applyRosterEdit(s, s.nextHandDraft, { type: "leave", occupantId: occId, reason: reason.trim() });
      } catch (e) { showSettleError(e.message); return; }
      renderRosterEditor(container);
    });
  }
  for (const btn of container.querySelectorAll(".roster-move")) {
    btn.addEventListener("click", () => {
      const occId = btn.dataset.occ;
      const target = window.prompt("移动到哪个空座位号？");
      if (!target) return;
      try {
        applyRosterEdit(s, s.nextHandDraft, { type: "move", occupantId: occId, targetSeatId: Number(target) });
      } catch (e) { showSettleError(e.message); return; }
      renderRosterEditor(container);
    });
  }
  renderPositionPreview(container.querySelector("#position-preview"));
}

function renderPositionPreview(container) {
  const s = rt.session;
  const edits = s.nextHandDraft ? s.nextHandDraft.rosterEdits : [];
  const rosterChanged = edits.length > 0;
  const preview = rosterChanged ? previewNextPositions(s, edits) : automaticNextPositions(s);
  if (rosterChanged && !rt.nextPositionsConfirmed) {
    container.innerHTML = `<div class="group-title">下一手位置预览 <span class="tip">（名单变化，需确认）</span></div>
      <p class="footnote">候选：BTN ${preview.buttonSeatId ?? "空"}号座 · SB ${preview.smallBlindSeatId ?? "空"} · BB ${preview.bigBlindSeatId}号座。与现场不符时可直接修改。</p>
      <div class="setup-row">
        <label>BTN 座位 <input type="number" id="pos-btn" min="1" max="${s.capacity}" value="${preview.buttonSeatId ?? ""}"></label>
        <label>SB 座位（留空=空小盲） <input type="number" id="pos-sb" min="1" max="${s.capacity}" value="${preview.smallBlindSeatId ?? ""}"></label>
        <label>BB 座位 <input type="number" id="pos-bb" min="1" max="${s.capacity}" value="${preview.bigBlindSeatId}"></label>
      </div>
      <label class="switch-line"><input type="checkbox" id="pos-confirm"> 我确认以上位置与现场一致</label>`;
    container.querySelector("#pos-confirm").addEventListener("change", (e) => {
      rt.nextPositionsConfirmed = e.target.checked;
      if (e.target.checked) {
        applyDraftPositions(s.nextHandDraft, {
          buttonSeatId: Number(container.querySelector("#pos-btn").value) || null,
          smallBlindSeatId: container.querySelector("#pos-sb").value === "" ? null : Number(container.querySelector("#pos-sb").value),
          bigBlindSeatId: Number(container.querySelector("#pos-bb").value) || null,
          source: "manual", confirmed: true,
        });
      }
      renderSettle();
    });
  } else {
    const p = s.nextHandDraft?.positions;
    container.innerHTML = `<p class="footnote">下一手位置：BTN ${p?.buttonSeatId ?? preview.buttonSeatId}号座 · SB ${p?.smallBlindSeatId ?? preview.smallBlindSeatId ?? "空"} · BB ${p?.bigBlindSeatId ?? preview.bigBlindSeatId}号座（${rosterChanged ? "已确认校准" : "自动轮转"}）</p>`;
  }
}

async function commitSettlement(area) {
  const s = rt.session;
  const btn = area.querySelector("#settle-commit");
  btn.disabled = true;
  try {
    const nextDraft = s.nextHandDraft || emptyNextHandDraft(s);
    if (!nextDraft.positions && !nextDraft.rosterEdits.length) {
      applyDraftPositions(nextDraft, { ...automaticNextPositions(s), confirmed: true });
    }
    s.nextHandDraft = nextDraft;
    const tx = beginCommit(s);                            // S02.1–2：同步校验
    await prepareRaw(buildNextContextForPrepare(s, tx));  // S02.5：后端验证下一手
    const committed = completeCommit(s, tx, null);        // S02.3–6：原子迁移
    rt.session = committed;
    rt.view = null;
    rt.advice = null;
    rt.adviceState = "idle";
    rt.nextPositionsConfirmed = false;
    rt.coord.resync(committed);
    persist();                                            // S02.7：成功才渲染新手
    renderAll();
    syncLearningJobs();                                   // A-04：本手学习记录
    if (rt.session.phase === "ready") {
      setStatus(`第 ${rt.session.currentHand.handNumber} 手开始，请选择本手底牌。`);
    }
  } catch (e) {
    showSettleError(`结算未完成：${e.message}`);          // 保留结算页与输入（S02 末段）
    btn.disabled = false;
  }
}

function buildNextContextForPrepare(s, tx) {
  // 临时构造下一手 context 供后端 prepare 验证（不落盘，S02.5）
  const clone = JSON.parse(JSON.stringify(s));
  for (const row of tx.settlement.rows) {
    const occ = clone.occupants[row.occupantId];
    if (occ) occ.confirmedChips = row.finalChips;
    if (row.finalChips === 0 && clone.occupants[row.occupantId]) {
      // 淘汰者从下一手名单移除由 completeCommit 的 leave 编辑处理；此处含 0 筹码也可 prepare
    }
  }
  for (const edit of tx.nextDraft.rosterEdits) {
    if (edit.type === "leave") {
      const seat = clone.seats.find((x) => x.occupantId === edit.occupantId);
      if (seat) seat.occupantId = null;
      delete clone.occupants[edit.occupantId];
    } else if (edit.type === "move") {
      const from = clone.seats.find((x) => x.occupantId === edit.occupantId);
      const to = clone.seats.find((x) => x.id === edit.targetSeatId);
      if (from) from.occupantId = null;
      if (to) to.occupantId = edit.occupantId;
    } else if (edit.type === "enter") {
      const seat = clone.seats.find((x) => x.id === edit.seatId);
      if (seat) seat.occupantId = edit.occupantId;
      clone.occupants[edit.occupantId] = {
        id: edit.occupantId, status: "active",
        confirmedChips: edit.chips, profileName: edit.name || null,
      };
    }
  }
  const draft = tx.nextDraft;
  clone.blindLevel = { ...draft.blindLevel };
  clone.learningEnabled = draft.learningEnabled;
  clone.icm = JSON.parse(JSON.stringify(draft.icm));
  const positions = draft.positions
    || automaticNextPositions({ seats: clone.seats, occupants: clone.occupants, positions: clone.positions, capacity: clone.capacity });
  clone.positions = positions;
  const participants = [];
  for (const seat of clone.seats) {
    if (!seat.occupantId) continue;
    const occ = clone.occupants[seat.occupantId];
    if (occ.status !== "active") continue;
    participants.push({ seat_id: seat.id, occupant_id: occ.id, starting_chips: occ.confirmedChips });
  }
  const profileNames = {};
  if (clone.learningEnabled) {
    for (const seat of clone.seats) {
      const occ = clone.occupants[seat.occupantId];
      if (occ?.status === "active" && occ.profileName) profileNames[occ.id] = occ.profileName;
    }
  }
  return {
    protocol_version: 2,
    hand_id: "preview-" + tx.transactionId,
    hand_number: s.handNumber + 1,
    context: {
      session_id: clone.sessionId,
      capacity: clone.capacity,
      hero_occupant_id: clone.heroOccupantId,
      participants,
      button_seat_id: positions.buttonSeatId,
      small_blind_seat_id: positions.smallBlindSeatId,
      big_blind_seat_id: positions.bigBlindSeatId,
      blinds: { sb: clone.blindLevel.sb, bb: clone.blindLevel.bb, ante_each: clone.blindLevel.anteEach },
      learning_enabled: clone.learningEnabled,
      profile_names: profileNames,
      icm: { scope: clone.icm.scope, payouts: clone.icm.payouts },
    },
  };
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
  $("#console").hidden = s.phase === "ended";
  $("#advice-panel").hidden = s.phase !== "playing" && s.phase !== "ready";
  $("#advanced-panel").hidden = false;
  $("#history-panel").hidden = false;
  $("#nextround-panel").hidden = s.phase !== "settling";
  if (s.phase !== "settling") $("#settle-area").hidden = true;
  renderSessionBar();
  renderTableCaption();
  renderSeats();
  renderBoard();
  renderConsole();
  renderAdvice();
  renderHistory();
  renderAdvanced();
  if (s.phase === "settling") renderSettle();
  renderDeck();
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
    const xy = seatXY(seat.id - 1, s.capacity);
    if (!occ || occ.status !== "active") {
      html.push(`<div class="seat empty" style="left:${xy.x}%;top:${xy.y}%" data-seat="${seat.id}">
        <div class="pos-tag">${seat.id}号座</div><div class="stack muted">空位</div></div>`);
      continue;
    }
    const vs = viewSeats?.get(seat.id);
    const roles = rolesForSeat(s, seat.id);
    const roleTag = roles.length ? `<span class="pos-tag">${roles.join("/")}</span>` : "";
    const isHero = occ.id === s.heroOccupantId;
    const stack = vs ? vs.stack : occ.confirmedChips;
    const folded = vs?.folded;
    const allIn = vs?.all_in;
    const acting = rt.view && rt.view.actor_seat_id === seat.id && !rt.view.hand_over;
    html.push(`<div class="seat${isHero ? " hero" : ""}${acting ? " acting" : ""}${folded ? " folded" : ""}" style="left:${xy.x}%;top:${xy.y}%" data-seat="${seat.id}">
      <div class="pos-tag">${seat.id}号座${isHero ? " · 你" : ""} ${roleTag}</div>
      <div class="stack">${chipsToBBText(stack, s.blindLevel.bb)}BB
        <span class="bb-tag">${fmtChips(stack)}</span></div>
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
  const html = [];
  for (let i = 0; i < 5; i++) {
    const c = cards[i];
    html.push(`<button type="button" class="slot static ${c ? "filled " + (IS_RED[c[1]] ? "red" : "black") : ""}"
      ${c ? "" : "disabled"} aria-label="公共牌${i + 1}">${c ? `<span class="r">${c[0]}</span><span class="s">${SUIT_GLYPH[c[1]]}</span>` : "+"}</button>`);
  }
  slots.innerHTML = html.join("");
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
  if (!s || !s.currentHand) return;
  const grid = $("#card-grid");
  const taken = new Set(rt.view?.taken || []);
  const heroCards = s.currentHand.heroCards;
  const deckTip = $("#deck-tip");
  if (rt.gridMode === "board") {
    deckTip.textContent = `（选择公共牌：已选 ${rt.boardPicks.length}/${nextBoardNeed()}，选满自动发牌）`;
  } else if (!heroCards[0] || !heroCards[1]) {
    deckTip.textContent = "（点选你的 2 张底牌）";
  } else {
    deckTip.textContent = "（点“发公共牌”进入选牌）";
  }
  const heroDone = !!heroCards[0] && !!heroCards[1];
  const html = [];
  for (const suit of SUITS) {
    for (const rank of RANKS) {
      const card = rank + suit;
      const isHeroPick = rt.gridMode !== "board" && heroCards.includes(card);
      const isBoardPick = rt.gridMode === "board" && rt.boardPicks.includes(card);
      const disabled = (!isHeroPick && !isBoardPick && taken.has(card)) || (rt.gridMode !== "board" && heroDone);
      html.push(`<button type="button" class="grid-card${IS_RED[suit] ? " red" : " black"}${isHeroPick || isBoardPick ? " picked" : ""}"
        data-card="${card}" ${disabled ? "disabled" : ""} aria-label="选择 ${card}">${rank}${SUIT_GLYPH[suit]}</button>`);
    }
  }
  grid.innerHTML = html.join("");
  for (const btn of grid.querySelectorAll(".grid-card:not([disabled])")) {
    btn.addEventListener("click", () => onDeckPick(btn.dataset.card));
  }
}

function nextBoardNeed() {
  return STREET_DEAL[rt.view?.street] || 0;
}

function onDeckPick(card) {
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
    return;
  }
  const hand = rt.session.currentHand;
  if (hand.heroCards[0] && hand.heroCards[1]) return;
  rt.heroPicks = [...rt.heroPicks, card].slice(-2);
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
}

/* --------------------------------------------------------------- 操作台 */

function setStatus(msg) { $("#status-line").textContent = msg; }

function renderConsole() {
  const s = rt.session;
  if (!s) return;
  const area = $("#action-area");
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
    setStatus("请选择你的 2 张底牌。");
    area.innerHTML = "";
    return;
  }
  if (!rt.view) {
    setStatus(rt.coord?.viewPending() ? "正在更新局面…" : "等待局面更新…");
    area.innerHTML = "";
    return;
  }
  const heroSeat = heroSeatOf(s);
  if (rt.view.hand_over) {
    setStatus("本手已结束，请核对全桌余额。");
    area.innerHTML = "";
    return;
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
    : `轮到你（${heroSeat}号座）行动 · 需跟注 ${fmtChips(toCall)} 筹码 / ${chipsToBBText(toCall, s.blindLevel.bb)}BB`);
  renderActionButtons(true);
}

function renderActionButtons(isHero) {
  const area = $("#action-area");
  const s = rt.session;
  const view = rt.view;
  if (!view) { area.innerHTML = ""; return; }
  const bb = s.blindLevel.bb;
  const pending = rt.coord.viewPending();
  const dis = pending ? "disabled" : "";
  const btn = (action, label, cls = "") =>
    `<button type="button" class="act-btn ${cls}" data-act="${action}" ${dis}>${label}</button>`;
  const seat = view.actor_seat_id;
  const toCall = view.to_call || 0;
  let html = btn(`fold:${seat}`, "弃牌")
    + btn(`call:${seat}`, toCall > 0 ? `跟注 ${fmtChips(toCall)} / ${chipsToBBText(toCall, bb)}BB` : "过牌");
  const minTo = view.min_raise_to;
  const maxTo = view.max_raise_to;
  if (minTo != null && maxTo != null && minTo <= maxTo) {
    html += btn(`allin:${seat}`, "全下", "allin")
      + `<span class="raise-box"><label>加注到
          <input type="number" id="raise-input" inputmode="numeric" value="${minTo}" min="${minTo}" max="${maxTo}">
          （${chipsToBBText(minTo, bb)}–${chipsToBBText(maxTo, bb)}BB）</label>
          <button type="button" class="act-btn" data-act="raise:${seat}" ${dis}>加注</button></span>`;
  }
  const need = nextBoardNeed();
  html += `<button type="button" class="ghost-btn" data-act="deal-board" ${pending || !need || rt.gridMode === "board" ? "disabled" : ""}>发${STREET_LABEL[view.street] || ""}（${need} 张）</button>`;
  html += `<button type="button" class="ghost-btn" data-act="undo" ${pending || !s.currentHand.ops.length ? "disabled" : ""}>撤销上一步</button>`;
  html += `<button type="button" class="ghost-btn" data-act="manual-close" ${dis}>结束本手，手动核对余额</button>`;
  area.innerHTML = html;
  for (const el of area.querySelectorAll("[data-act]")) {
    el.addEventListener("click", () => onActButton(el.dataset.act));
  }
  area.querySelector("#raise-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); onActButton(`raise:${view.actor_seat_id}`); }
  });
}

function onActButton(spec) {
  const view = rt.view;
  if (!view) return;
  const [action, seatStr] = spec.split(":");
  const seatId = Number(seatStr) || view.actor_seat_id;
  if (action === "fold") return submitAction({ op: "action", seat_id: seatId, type: "fold" });
  if (action === "call") return submitAction({ op: "action", seat_id: seatId, type: view.to_call > 0 ? "call" : "check" });
  if (action === "allin") return submitAction({ op: "action", seat_id: seatId, type: "allin" });
  if (action === "raise") {
    const input = $("#raise-input");
    const to = Number(input?.value);
    if (!Number.isSafeInteger(to)) return setStatus("加注额必须为整数筹码");
    return submitAction({ op: "action", seat_id: seatId, type: "raise", to });
  }
  if (action === "undo") return onUndo();
  if (action === "manual-close") return onManualClose();
  if (action === "deal-board") {
    rt.gridMode = "board";
    rt.boardPicks = [];
    $("#deck-panel").open = true;
    renderDeck();
    $("#deck-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function renderAdvice() {
  const s = rt.session;
  const box = $("#advice");
  const note = $("#advice-note");
  if (!s || s.phase !== "playing" || !rt.view || rt.view.hand_over
    || rt.view.actor_seat_id !== heroSeatOf(s)) {
    const stateText = {
      idle: "轮到你行动时，这里给出与最新录入局面匹配的推演。",
      pending: "正在推演…（不影响继续录入动作）",
      failed: "推演失败。",
    }[rt.adviceState];
    box.innerHTML = `<p class="empty">${stateText}</p>${rt.adviceState === "failed"
      ? '<button type="button" class="ghost-btn" data-action="retry-advice">重试推演</button>' : ""}`;
    box.querySelector("[data-action=retry-advice]")?.addEventListener("click", () => maybeAdvice());
    note.textContent = "";
    return;
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
      <tr><td>跟注 EV</td><td class="${adv.ev_call >= 0 ? "pos" : "neg"}">${adv.ev_call >= 0 ? "+" : ""}${chipsToBBText(adv.ev_call, bb)} BB</td></tr>
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
  sw.checked = s.learningEnabled;
  if (!sw.dataset.bound) {
    sw.dataset.bound = "1";
    sw.addEventListener("change", () => {
      if (!rt.session.nextHandDraft) {
        rt.session.nextHandDraft = emptyNextHandDraft(rt.session);
      }
      rt.session.nextHandDraft.learningEnabled = sw.checked;
      persist();
    });
  }
  let unitSel = $("#tg-unit-live");
  if (!unitSel) {
    const wrap = document.createElement("div");
    wrap.className = "setup-row";
    wrap.innerHTML = `<label>显示单位
      <select id="tg-unit-live"><option value="bb">BB 优先</option>
      <option value="chips">筹码优先</option></select></label>
      <button type="button" class="ghost-btn" id="tg-export">导出会话</button>
      <button type="button" class="ghost-btn" id="tg-end-session">结束本桌</button>`;
    $("#advanced-body").prepend(wrap);
    unitSel = wrap.querySelector("#tg-unit-live");
    unitSel.value = rt.unit;
    unitSel.addEventListener("change", (e) => {
      rt.unit = e.target.value;
      rt.session.displayUnit = rt.unit;
      persist();
      renderAll();
    });
    wrap.querySelector("#tg-export").addEventListener("click", () => {
      downloadRaw(JSON.stringify(rt.session, null, 2));
    });
    wrap.querySelector("#tg-end-session").addEventListener("click", () => {
      if (!window.confirm("结束当前牌桌？已确认的余额与本桌记录将保留。")) return;
      import("./session.js").then((S) => {
        S.endSession(rt.session);
        persist();
        renderAll();
      });
    });
  }
}

/* --------------------------------------------------------------- 绑定 */

function bindGlobal() {
  $("#tg-capacity").addEventListener("change", renderSetupSeats);
  $("#tg-unit").addEventListener("change", renderSetupSeats);
  $("#tg-create").addEventListener("click", onPreviewTable);
  $("#tg-confirm").addEventListener("click", onConfirmTable);
  Store.onStorageChange((newValue) => {
    if (rt.conflict) return;
    const mine = rt.session ? JSON.stringify(rt.session) : null;
    if (newValue !== null && newValue !== mine) {
      rt.conflict = true;
      if (window.confirm("另一个标签页更新了本会话。载入最新版本？")) {
        location.reload();
      } else {
        setStatus("注意：本地会话已在其他标签页被修改，本页写入将被阻止。");
      }
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
