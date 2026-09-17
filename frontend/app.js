/* 大魔丸 · 6人桌 MTT 决策辅助（原生 JS，只与本机服务通信） */
"use strict";

const RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"];
const SUITS = ["s", "h", "d", "c"];
const SUIT_GLYPH = { s: "♠", h: "♥", d: "♦", c: "♣" };
const SUIT_NAME = { s: "黑桃", h: "红心", d: "方块", c: "梅花" };
const IS_RED = { s: false, h: true, d: true, c: false };
const POSITIONS_BY_SIZE = {
  6: ["SB", "BB", "UTG", "HJ", "CO", "BTN"],
  8: ["SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO", "BTN"],
  9: ["SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN"],
};
const STREET_DEAL = { flop: 3, turn: 1, river: 1 };
const STREET_LABEL = { flop: "翻牌", turn: "转牌", river: "河牌" };
let ITERATIONS = 50000;
function tuneIterations() {
  // 模拟成本随人数线性涨：按桌型缩放，保证建议在几秒内返回
  ITERATIONS = Math.max(10000, Math.round(170000 / state.config.player_count));
}

const CONFIG_KEY = "paishi_config_v2";   // v2：公开上线重置一次，回到默认 6 人桌
const NAMES_KEY = "paishi_names_v1";

function loadNames() {
  try { return JSON.parse(localStorage.getItem(NAMES_KEY) || "{}"); }
  catch (_) { return {}; }
}
function saveNames(d) {
  try { localStorage.setItem(NAMES_KEY, JSON.stringify(d)); } catch (_) {}
}
function opponentName(pos) {
  return loadNames()[`${state.config.player_count}:${pos}`] || "";
}
function setOpponentName(pos, name) {
  const all = loadNames();
  all[`${state.config.player_count}:${pos}`] = name;
  saveNames(all);
}
function loadConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIG_KEY) || "null");
    if (saved && POSITIONS_BY_SIZE[saved.player_count] && saved.sb > 0 && saved.bb > 0) {
      return saved;
    }
  } catch (_) { /* 忽略损坏的存档 */ }
  return { player_count: 6, sb: 100, bb: 200, ante: 25, stack: 10000, stacks: null, payouts: [] };
}
function saveConfig() {
  try { localStorage.setItem(CONFIG_KEY, JSON.stringify(state.config)); } catch (_) {}
}

const state = {
  config: loadConfig(),
  heroPos: "BTN",
  heroCards: [null, null],
  ops: [],
  view: null,
  advice: null,
  gridMode: null,        // 'hero' | 'board'
  boardPicks: [],
  busy: false,
  pending: null,
  recorded: false,     // 本手是否已计入对手统计         // 忙碌期间用户点击的操作，处理完自动补上
  error: null,           // 最近一次请求错误（界面优先显示，直到下一次成功）
};

const $ = (sel) => document.querySelector(sel);
const money = (n) => Number(n).toLocaleString("zh-CN");
const bb = (chips) => (chips / state.config.bb).toFixed(1);          // 筹码 → BB 显示
const bbToChips = (v) => Math.round(Number(v) * state.config.bb);    // BB 输入 → 筹码

/* ---------- 牌面组件 ---------- */

function cardBtn(card, disabled, label) {
  const cls = `grid-card ${IS_RED[card[1]] ? "red" : "black"}`;
  const dis = disabled ? "disabled" : "";
  return `<button type="button" class="${cls}" ${dis} data-action="card"
    data-card="${card}" aria-label="${label}">${card[0]}${SUIT_GLYPH[card[1]]}</button>`;
}

function renderGrid() {
  const taken = new Set();
  if (state.view) (state.view.taken || []).forEach((c) => taken.add(c));
  state.heroCards.forEach((c) => c && taken.add(c));
  const boardMode = state.gridMode === "board";
  state.boardPicks.forEach((c) => taken.add(c));   // 已选入本街的牌同样灰掉
  const html = SUITS.map((s) =>
    RANKS.map((r) => {
      const card = r + s;
      if (!boardMode) return cardBtn(card, false, `选择 ${r}${SUIT_NAME[s]}`);
      const picked = state.boardPicks.includes(card);
      const cls = `grid-card ${IS_RED[card[1]] ? "red" : "black"}${picked ? " picked" : ""}`;
      const dis = taken.has(card) && !picked ? "disabled" : "";
      return `<button type="button" class="${cls}" ${dis} data-action="card"
        data-card="${card}" aria-label="${picked ? "已选" : "选为公共牌"} ${r}${SUIT_NAME[s]}">${card[0]}${SUIT_GLYPH[card[1]]}</button>`;
    }).join("")
  ).join("");
  $("#card-grid").innerHTML = html;
}

function updateDeckTip() {
  const tip = $("#deck-tip");
  if (!tip) return;
  if (state.gridMode === "board") {
    const need = STREET_DEAL[state.view.street] || 0;
    tip.textContent = `（点选 ${need} 张公共牌：已选 ${state.boardPicks.length}/${need}，金框为已选）`;
  } else {
    tip.textContent = "（点选底牌与公共牌，灰牌已发出）";
  }
}

function renderHeroSlots() {
  $("#hero-slots").innerHTML = state.heroCards.map((c, i) => {
    const act = `data-action="hero-pick" data-idx="${i}"`;
    if (c) {
      const cls = `slot filled ${IS_RED[c[1]] ? "red" : "black"}`;
      return `<button type="button" class="${cls}" ${act}
        aria-label="清除 ${c[0]}${SUIT_NAME[c[1]]}"><span class="r">${c[0]}</span><span class="s">${SUIT_GLYPH[c[1]]}</span></button>`;
    }
    return `<button type="button" class="slot" ${act} aria-label="选择第 ${i + 1} 张底牌">+</button>`;
  }).join("");
}

/* ---------- 牌桌与操作台 ---------- */

function currentStacks() {
  if (state.config.stacks && state.config.stacks.length === state.config.player_count) {
    return [...state.config.stacks];
  }
  return Array(state.config.player_count).fill(state.config.stack);
}

function renderStackInputs() {
  const names = POSITIONS_BY_SIZE[state.config.player_count];
  const stacks = currentStacks();
  const inputs = document.querySelectorAll('#stack-grid input[data-stack-idx]');
  const unchanged = inputs.length === names.length &&
    Array.from(inputs).every((el, i) => el.value === String(stacks[i]));
  if (unchanged) return;   // 值一致就不动 DOM，避免打断编辑
  $("#stack-grid").innerHTML = names.map((p, i) =>
    `<label class="stack-cell">${p}<input type="number" min="1" data-stack-idx="${i}" value="${stacks[i]}" aria-label="${p} 筹码"></label>`).join("");
}

function seatXY(i, n) {
  const angle = (90 + (i * 360) / n) * Math.PI / 180;  // BTN 在底部，按桌型均分
  return { left: 50 + 40 * Math.cos(angle), top: 50 + 37 * Math.sin(angle) };
}

function renderSeats() {
  const n = state.config.player_count;
  const names = POSITIONS_BY_SIZE[n];
  const layer = $("#seat-layer");
  const seatsHtml = names.map((pos, i) => {
    const xy = seatXY(i, n);
    let inner = `<div class="pos-tag">${pos}</div>`;
    let cls = "seat";
    if (state.view) {
      const seat = state.view.seats.find((s) => s.pos === pos);
      if (seat) {
        const acting = state.view.actor === pos;
        const folded = !seat.in_hand && !state.view.hand_over;
        const allin = seat.in_hand && seat.stack === 0;
        const badges = [];
        if (pos === "BTN") badges.push('<span class="chip chip-d">D</span>');
        if (pos === "SB") badges.push('<span class="chip chip-sb">SB</span>');
        if (pos === "BB") badges.push('<span class="chip chip-bb">BB</span>');
        if (folded) badges.push('<span class="badge badge-fold">弃</span>');
        if (allin) badges.push('<span class="badge badge-allin">全下</span>');
        if (seat.bet > 0 && !folded) badges.push(`<span class="bet-chip">${bb(seat.bet)} BB</span>`);
        const revealedCards = state.view.hand_over && state.view.revealed && state.view.revealed[pos];
        const cardsHtml = seat.is_hero && state.heroCards[0]
          ? `<div class="seat-cards">${state.heroCards.map((c) =>
              `<span class="mini-card ${IS_RED[c[1]] ? "red" : ""}">${c[0]}${SUIT_GLYPH[c[1]]}</span>`).join("")}</div>`
          : (revealedCards
              ? `<div class="seat-cards" title="摊牌亮牌">${revealedCards.map((c) =>
                  `<span class="mini-card ${IS_RED[c[1]] ? "red" : ""}">${c[0]}${SUIT_GLYPH[c[1]]}</span>`).join("")}</div>`
              : "");
        cls += `${acting ? " acting" : ""}${folded ? " folded" : ""}${seat.is_hero ? " hero" : ""}`;
        inner = `<div class="pos-tag">${pos}${seat.is_hero ? " · 你" : ""}</div>
          <div class="stack">${bb(seat.stack)}<small class="bb-tag"> BB</small></div>
          <div class="badges">${badges.join("")}</div>${cardsHtml}`;
      }
    }
    const xyStyle = state.view
      ? `left:${xy.left.toFixed(1)}%;top:${xy.top.toFixed(1)}%;`
      : `left:${xy.left.toFixed(1)}%;top:${xy.top.toFixed(1)}%;`;
    return `<div class="${cls}" style="${xyStyle}">${inner}</div>`;
  }).join("");
  layer.innerHTML = seatsHtml;
  $("#pot-num").textContent = state.view ? bb(state.view.pot) + " BB" : "0 BB";
  $("#street-caption").textContent = state.view ? `阶段：${{ preflop: "翻牌前", flop: "翻牌", turn: "转牌", river: "河牌", over: "结束" }[state.view.street]}` : "";
}

function renderBoard() {
  const board = state.view ? state.view.board : [];
  const preview = board.concat(state.boardPicks);   // 选牌中的牌即时上桌预览
  $("#board-slots").innerHTML = [0, 1, 2, 3, 4].map((i) => {
    const c = preview[i];
    if (c) {
      const pending = i >= board.length ? " pending" : "";
      return `<div class="slot filled ${IS_RED[c[1]] ? "red" : "black"} static${pending}"><span class="r">${c[0]}</span><span class="s">${SUIT_GLYPH[c[1]]}</span></div>`;
    }
    return '<div class="slot static" aria-hidden="true"></div>';
  }).join("");
}

function renderConsole() {
  const area = $("#action-area");
  const line = $("#status-line");
  line.classList.remove("err");
  // 按下注轮分组展示：每条公共牌是一条轮次的分界
  const STREETS = ["翻牌前", "翻牌", "转牌", "河牌"];
  const fmtCard = (c) => `<b class="cb ${IS_RED[c[1]] ? "red" : "black"}">${c[0]}${SUIT_GLYPH[c[1]]}</b>`;
  const rounds = [];
  let chips = [];
  let boardCount = 0;
  let n = 0;
  const flush = () => {
    if (!chips.length) return;
    rounds.push(`<div class="ops-round"><span class="round-tag">${STREETS[boardCount] || ""}</span>${chips.join("")}</div>`);
    chips = [];
  };
  state.ops.forEach((op) => {
    if (op.op === "board") {
      flush();
      rounds.push(`<div class="ops-board">${STREETS[boardCount + 1] || "公共牌"} ${op.cards.map(fmtCard).join(" ")}</div>`);
      boardCount += 1;
    } else {
      n += 1;
      const txt = { fold: "弃牌", check: "过牌", call: "跟注", raise: `加注到 ${bb(op.to || 0)} BB`, allin: "全下" }[op.type];
      chips.push(`<span class="op-chip">${n}. ${op.seat} ${txt}</span>`);
    }
  });
  flush();
  $("#ops-line").innerHTML = rounds.join("") + (state.ops.length
    ? `<button type="button" class="ghost-btn" data-action="undo">撤销上一步</button>`
    : "");

  if (!state.view) {
    if (state.error) { area.innerHTML = ""; return; }   // 保留错误提示不被覆盖
    line.textContent = state.heroCards.every(Boolean)
      ? "底牌已选好，正在请求本局数据…"
      : "先点下方牌面网格，选你的 2 张底牌。";
    area.innerHTML = "";
    return;
  }

  if (state.view.hand_over) {
    const pay = state.view.payoffs || {};
    line.textContent = "手牌结束 — 各家盈亏：" +
      Object.entries(pay).map(([p, v]) => `${p} ${v > 0 ? "+" : ""}${money(v)}`).join("，") || "无变动";
    area.innerHTML = `<button type="button" class="primary-btn" data-action="reset">再来一手</button>`;
    return;
  }

  if (!state.view.actor) {
    const n = STREET_DEAL[state.view.street];
    const label = STREET_LABEL[state.view.street] || "公共牌";
    line.textContent = `本轮下注结束，发${label}（${n} 张）`;
    area.innerHTML = `<button type="button" class="ghost-btn" data-action="deal-toggle">
        ${state.gridMode === "board" ? "收起牌面" : `发${label}`}</button>`;
    return;
  }

  const isHero = state.view.actor === state.heroPos;
  line.innerHTML = `轮到 <b>${state.view.actor}${isHero ? "（你）" : ""}</b>` +
    (state.view.to_call > 0 ? ` · 需跟注 ${bb(state.view.to_call)} BB` : " · 无注");

  const tc = state.view.to_call;
  const dis = state.busy ? "disabled" : "";
  const canRaise = state.view.min_raise_to != null && state.view.max_raise_to != null
    && state.view.max_raise_to > state.view.to_call;
  const clampTo = (chips) => Math.max(state.view.min_raise_to, Math.min(state.view.max_raise_to, chips));
  const presets = {
    min: state.view.min_raise_to,
    half: clampTo(tc + Math.round(state.view.pot * 0.5)),
    pot: clampTo(tc + state.view.pot),
  };
    const foldBtn = tc > 0
    ? `<button type="button" class="act-btn fold" ${dis} data-action="do-fold">弃牌</button>`
    : "";   // 面前无注时规则上不允许弃牌，只提供过牌/加注
  const raiseBtns = canRaise ? `
      <button type="button" class="act-btn raise" ${dis} data-action="do-raise-to" data-to="${presets.min}" title="加注到最小加注额">加注 ${bb(presets.min)} BB</button>
      <button type="button" class="act-btn raise" ${dis} data-action="do-raise-to" data-to="${presets.half}" title="下注约半个底池">半池 ${bb(presets.half)} BB</button>
      <button type="button" class="act-btn raise" ${dis} data-action="do-raise-to" data-to="${presets.pot}" title="下注约一个底池">满池 ${bb(presets.pot)} BB</button>` : "";
  const raiseRow = canRaise ? `
    <div class="raise-row">
      <input type="number" id="raise-to" step="0.1" value="${bb(presets.min)}" min="${bb(state.view.min_raise_to)}" max="${bb(state.view.max_raise_to)}" aria-label="自定义加注到（BB），回车确认" ${dis}> BB
      <button type="button" class="ghost-btn" ${dis} data-action="do-raise-input">按输入值加注（回车）</button>
    </div>` : "";
  const allinBtn = canRaise
    ? `<button type="button" class="act-btn allin" ${dis} data-action="do-allin">全下 ${bb(state.view.max_raise_to)} BB</button>`
    : "";   // 面对全下时全下≡跟注，隐藏以免误导（旧版此处渲染"全下 0.0 BB"）
  area.innerHTML = `
    <div class="action-row">
      ${foldBtn}
      <button type="button" class="act-btn call" ${dis} data-action="do-call">${tc > 0 ? `跟注 ${bb(tc)} BB` : "过牌"}</button>
      ${raiseBtns}
      ${allinBtn}
    </div>
    ${raiseRow}`;
}

async function refreshLearn() {
  try {
    const r = await fetch("/api/stats/summary/all");
    const d = await r.json();
    const posStr = Object.entries(d.pool || {}).map(([p, v]) => p + ":" + v.hands).join(" · ");
    $("#learn-line").textContent =
      "已积累 " + d.total_hands + " 手人群数据" + (posStr ? "（" + posStr + "）" : "") +
      (Object.keys(d.named || {}).length ? " · 具名档案 " + Object.keys(d.named).length : "");
  } catch (_) { /* 静默 */ }
}

/* ---------- 建议面板 ---------- */

function renderAdvice() {
  const box = $("#advice");
  const a = state.advice;
  $("#advice-note").textContent = a ? "范围自动估算" : "";
  if (!a) {
    const heroActing = state.view && state.view.actor === state.heroPos && !state.view.hand_over;
    box.innerHTML = heroActing
      ? '<p class="empty">计算中…</p>'
      : '<p class="empty">轮到你行动时，这里给出胜率与赔率分析。</p>';
    return;
  }
  const eq = a.equity;
  const rec = a.recommendation;
  const mixHtml = (mix) => mix.map((m) => `
    <div class="mix-row">
      <span class="mix-label">${m.label}</span>
      <span class="mix-bar" role="img" aria-label="${m.label} ${m.pct}%">
        <i style="width:${Math.max(2, Math.min(100, m.pct))}%"></i>
      </span>
      <span class="mix-pct">${m.pct}%</span>
    </div>`).join("");
  const recHtml = `
    <div class="rec-box">
      <div class="rec-title">行动建议（启发式）</div>
      <div class="rec-primary">${rec.primary}</div>
      ${mixHtml(rec.mix)}
      <p class="rec-reason">${rec.reason}</p>
    </div>`;
  let cfrHtml = "";
  const c = a.cfr;
  if (c && c.supported) {
    cfrHtml = `
      <div class="cfr-box">
        <div class="cfr-title">第三层 · CFR 均衡参考
          <span class="cfr-meta">${c.street || "河牌"}${c.approximate ? "（近似）" : ""} · ${c.iterations} 次迭代 · ${c.hero_range_combos}×${c.villain_range_combos} 组合${c.range_capped ? "（已抽样）" : ""}</span>
        </div>
        ${mixHtml(c.mix)}
        <p class="cfr-note">你的手牌相对对手范围权益约 ${c.equity_vs_range}%（${c.buckets} 桶抽象）。${c.agree ? "与启发式方向一致。" : "与启发式主建议方向不同：此处是范围层面的均衡频率，供交叉参考。"}</p>
      </div>`;
  } else if (c && !c.supported && c.reason && !c.reason.includes("河牌")) {
    cfrHtml = `<p class="footnote">CFR 参考：${c.reason}</p>`;
  }
  box.innerHTML = `
    <div class="big">${eq.win.toFixed(1)}<small> % 胜率（含平 ${eq.tie.toFixed(1)}%）</small></div>
    <div class="bar" role="img" aria-label="胜 ${eq.win}% 平 ${eq.tie}% 负 ${eq.lose}%">
      <i class="w" style="width:${eq.win}%"></i><i class="t" style="width:${eq.tie}%"></i><i class="l" style="width:${eq.lose}%"></i>
    </div>
    ${recHtml}
    <table class="adv-table">
      <tr><td>需跟注</td><td>${a.to_call > 0 ? bb(a.to_call) + " BB" : "0（可过牌）"}</td></tr>
      <tr><td>跟注所需胜率</td><td>${a.required_eq}%</td></tr>
      <tr><td>你的权益</td><td>${(eq.win + eq.tie / 2).toFixed(1)}%</td></tr>
      <tr><td>跟注 EV</td><td class="${a.ev_call >= 0 ? "pos" : "neg"}">${a.ev_call >= 0 ? "+" : ""}${bb(a.ev_call)} BB</td></tr>
      <tr><td>SPR</td><td>${rec.spr ?? "—"}${rec.spr_note ? " · " + rec.spr_note : ""}</td></tr>
      <tr><td>M 值</td><td>${(a.m_value !== null && a.m_value !== undefined) ? a.m_value : "—"}</td></tr>
    </table>
    <div class="range-list">${a.opponents.map((o) => {
      const name = o.name ? `<b>${o.name}</b>（${o.pos}）` : `<b>${o.pos}</b>`;
      const stat = o.stats && o.stats.hands >= 5
        ? ` · VPIP ${o.stats.vpip_pct}% / PFR ${o.stats.pfr_pct}%` : "";
      const narrow = o.narrowed ? " · 范围已按 PFR 收窄" : "";
      return `<div class="range-item">${name}：${o.situation} · ${o.combos} 组合${stat}${narrow} · 剩余 ${bb(o.stack)} BB</div>`;
    }).join("")}</div>
    ${cfrHtml}
    <p class="footnote">${a.note} · 模拟 ${money(eq.iterations)} 手 · ${eq.elapsedMs} ms</p>`;
}

function commitRaiseInput() {
  if (!state.view || state.view.actor == null) return;
  const to = clampToChips(Number($("#raise-to").value));
  pushOp({ op: "action", type: "raise", to, seat: state.view.actor });
}

function clampToChips(v) {
  const lo = state.view.min_raise_to, hi = state.view.max_raise_to;
  return Math.max(lo, Math.min(hi, bbToChips(v)));
}

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.target && ev.target.id === "raise-to") {
    ev.preventDefault();
    commitRaiseInput();
  }
});

/* ---------- 与服务端交互 ---------- */

function payload() {
  return {
    config: { player_count: state.config.player_count, sb: state.config.sb, bb: state.config.bb, ante: state.config.ante,
              stacks: currentStacks(), payouts: state.config.payouts || [] },
    hero_pos: state.heroPos,
    hero_cards: state.heroCards,
    ops: state.ops,
    iterations: ITERATIONS,
    names: Object.fromEntries(
      POSITIONS_BY_SIZE[state.config.player_count]
        .map((p) => [p, opponentName(p)])
    ),
  };
}

function showError(msg) {
  state.error = msg;
  const line = $("#status-line");
  line.classList.add("err");
  const hint = "（可点「撤销上一步」回退）";
  line.textContent = msg.includes(hint) ? msg : msg + hint;
}

function pushOp(op) {
  if (state.busy) { state.pending = op; return; }          // 忙碌：暂存，处理完自动补上
  if (op.op === "action") {
    if (!state.view || state.view.hand_over || state.view.actor !== op.seat) return;  // 行动者校验
  }
  if (op.op === "board" && state.view && state.view.actor) return;
  state.error = null;
  state.ops.push(op);
  state.busy = true;         // 点击即刻禁用操作按钮，不等网络返回（防连点）
  renderConsole();
  refreshCore();
}

async function refresh(retries = 0) {
  if (!state.heroCards.every(Boolean)) { renderAll(); return; }
  if (state.busy) return;                    // 防重入
  state.busy = true;
  return refreshCore(retries);
}

async function refreshCore(retries = 0) {
  try {
    const r = await fetch("/api/hand/view", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    const data = await r.json();
    if (!r.ok) {
      // 自愈：最后一步操作非法（常见于连点），撤销后重试
      if (state.ops.length > 0 && retries < 3) {
        state.ops.pop();
        state.busy = false;
        return refreshCore(retries + 1);
      }
      showError((data.detail || `请求失败（${r.status}）`) + "（可点「撤销上一步」回退）");
      state.busy = false;
      renderAll();
      return;
    }
    state.error = null;
    state.view = data;
    state.advice = null;
    state.busy = false;
    if (data.hand_over && !state.recorded) {
      state.recorded = true;
      fetch("/api/stats/record", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      }).then(() => refreshLearn()).catch(() => {});
    }
    renderAll();
    if (data.actor === state.heroPos && !data.hand_over) {
      const r2 = await fetch("/api/hand/advice", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      if (r2.ok) {
        state.advice = (await r2.json()).advice;
        renderAdvice();
      }
    }
  } catch (_) {
    state.busy = false;
    showError("无法连接本机服务 — 请确认服务正在运行");
    renderAll();
  }
  if (state.pending) {
    const op = state.pending;
    state.pending = null;
    pushOp(op);
  }
}

/* ---------- 事件 ---------- */

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-action]");
  if (!btn) return;
  if (state.busy) return;
  const action = btn.dataset.action;

  switch (action) {
    case "card": {
      const card = btn.dataset.card;
      if (state.gridMode === "board") {
        if (!state.boardPicks.includes(card)) state.boardPicks.push(card);
        const need = STREET_DEAL[state.view.street];
        if (state.boardPicks.length >= need) {
          state.ops.push({ op: "board", cards: state.boardPicks.slice(0, need) });
          state.boardPicks = [];
          state.gridMode = null;
          refresh();
        } else {
          renderGrid();
          renderBoard();
          updateDeckTip();
        }
      } else if (AppLogic.heroPickAllowed(state)) {
        const idx = state.heroCards.findIndex((c) => !c);
        state.heroCards[idx] = card;
        refresh();   // 仅在底牌未选齐时生效；选满后点牌库一律忽略，防误触重开
      }
      return;
    }
    case "hero-pick": {
      const idx = Number(btn.dataset.idx);
      if (state.heroCards[idx]) {
        state.heroCards[idx] = null;
        state.ops = []; state.view = null; state.advice = null;
      }
      refresh(); return;
    }
    case "hero-clear": {
      state.heroCards[Number(btn.dataset.idx)] = null;
      state.ops = []; state.view = null; state.advice = null;
      refresh(); return;
    }
    case "deal-toggle": {
      state.gridMode = state.gridMode === "board" ? null : "board";
      state.boardPicks = [];
      renderGrid(); renderBoard(); renderConsole(); updateDeckTip(); return;
    }
    case "do-fold": pushOp({ op: "action", type: "fold", seat: state.view.actor }); return;
    case "do-call": pushOp({ op: "action", type: state.view.to_call > 0 ? "call" : "check", seat: state.view.actor }); return;
    case "do-allin": pushOp({ op: "action", type: "allin", seat: state.view.actor }); return;
    case "do-raise-to": {
      // data-to 已是筹码值；仅夹在合法范围内，不能再按 BB 换算
      const v = Number(btn.dataset.to);
      const to = Math.max(state.view.min_raise_to, Math.min(state.view.max_raise_to, v));
      pushOp({ op: "action", type: "raise", to, seat: state.view.actor });
      return;
    }
    case "do-raise-input": commitRaiseInput(); return;
    case "reopen":
      // 全新重开：筹码恢复默认 + 清空手牌与操作记录
      state.config.stacks = null;
      saveConfig();
      state.ops = []; state.heroCards = [null, null]; state.view = null;
      state.advice = null; state.pending = null; state.error = null; state.gridMode = "hero";
      refresh();
      return;
    case "stats-reset":
      if (confirm("确定清空全部学习数据？")) {
        fetch("/api/stats/reset", { method: "POST" }).then(() => refreshLearn());
      }
      return;
    case "undo": state.ops.pop(); state.pending = null; state.gridMode = null; state.boardPicks = []; renderGrid(); renderBoard(); updateDeckTip(); refresh(); return;
    case "reset":
      state.ops = []; state.heroCards = [null, null]; state.view = null;
      state.advice = null; state.pending = null; state.recorded = false; state.gridMode = "hero";
      refresh(); return;
  }
});

function rebuildPosOptions(n) {
  const sel = $("#cfg-pos");
  const names = POSITIONS_BY_SIZE[n];
  sel.innerHTML = names.map((p) => `<option>${p}</option>`).join("");
  sel.value = names[names.length - 1];   // 默认 BTN
  state.heroPos = sel.value;
}

// 各座位筹码：实时提交（每次输入立即保存，页面重载不丢失）
$("#names-grid").addEventListener("input", (ev) => {
  const input = ev.target.closest("input[data-name-pos]");
  if (!input) return;
  setOpponentName(input.dataset.namePos, input.value.trim());
});

$("#stack-grid").addEventListener("input", (ev) => {
  const input = ev.target.closest("input[data-stack-idx]");
  if (!input) return;
  const stacks = currentStacks();
  stacks[Number(input.dataset.stackIdx)] = Math.max(1, Number(input.value) || 1);
  state.config.stacks = stacks;
  saveConfig();
});

$("#cfg-size").addEventListener("change", () => {
  state.config.player_count = Number($("#cfg-size").value);
  saveConfig();
  tuneIterations();
  rebuildPosOptions(state.config.player_count);
  state.ops = []; state.heroCards = [null, null]; state.view = null;
  state.advice = null; state.pending = null; state.error = null;
  refresh();
});

$("#cfg-payouts").addEventListener("change", () => {
  const raw = $("#cfg-payouts").value.trim();
  const nums = raw ? raw.split(/[，,\s]+/).map(Number).filter((n) => !Number.isNaN(n) && n >= 0) : [];
  state.config.payouts = nums;
  saveConfig();
  refresh();
});

["cfg-sb", "cfg-bb", "cfg-ante", "cfg-stack"].forEach((id) =>
  $("#" + id).addEventListener("change", () => {
    state.config = {
      player_count: state.config.player_count,
      sb: Number($("#cfg-sb").value) || 100,
      bb: Number($("#cfg-bb").value) || 200,
      ante: Number($("#cfg-ante").value) || 0,
      stack: Number($("#cfg-stack").value) || 10000,
      stacks: null,
    };
    saveConfig();
    state.ops = []; state.heroCards = [null, null]; state.view = null; state.advice = null;
    refresh();
  })
);

$("#cfg-pos").addEventListener("change", () => {
  state.heroPos = $("#cfg-pos").value;   // 只换座位，手牌保留，设置顺序无关
  state.ops = []; state.view = null; state.advice = null; state.pending = null;
  state.recorded = false;
  renderNames();
  refresh();
});

/* ---------- 渲染总入口与启动 ---------- */

function renderNames() {
  const names = POSITIONS_BY_SIZE[state.config.player_count]
    .filter((p) => p !== state.heroPos);
  $("#names-grid").innerHTML = names.map((p) => {
    const v = opponentName(p);
    return `<label class="stack-cell">${p}<input type="text" data-name-pos="${p}" value="${v}" placeholder="—" aria-label="${p} 对手代号"></label>`;
  }).join("");
}

function renderIcm() {
  const el = $("#icm-panel");
  if (!el) return;
  const icm = state.view && state.view.icm;
  if (!icm) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  if (icm.error) { el.innerHTML = `<p class="footnote">ICM：${icm.error}</p>`; return; }
  const rows = icm.rows.map((r) =>
    `<tr class="${r.hero ? "icm-hero" : ""}"><td>${r.pos}${r.hero ? "（你）" : ""}</td>` +
    `<td>${money(r.stack)}</td><td>${r.pct}%</td><td>${money(r.equity)}</td></tr>`).join("");
  el.innerHTML = `
    <div class="icm-title">ICM 奖金期望 <span class="tip">（奖池 ${money(icm.total)} · Malmuth–Harville）</span></div>
    <table class="icm-table"><thead><tr><th>座位</th><th>记分牌</th><th>份额</th><th>期望奖金</th></tr></thead>
    <tbody>${rows}</tbody></table>
    <p class="footnote">泡沫期中短筹码的边缘牌跟注价值低于记分牌 EV，淘汰风险要计入决策。</p>`;
}

function renderZoneFocus() {
  const zone = AppLogic.nextZone(state);
  [["setup", "setup"], ["console", "console"], ["deck-panel", "deck"]].forEach(([id, z]) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("zone-focus", z === zone);
  });
}

function renderAll() {
  renderIcm();
  renderZoneFocus();
  renderHeroSlots();
  renderNames();
  renderStackInputs();
  renderGrid();
  updateDeckTip();
  renderSeats();
  renderBoard();
  renderConsole();
  renderAdvice();
}

$("#cfg-size").value = String(state.config.player_count);
rebuildPosOptions(state.config.player_count);
$("#cfg-sb").value = state.config.sb;
$("#cfg-bb").value = state.config.bb;
$("#cfg-ante").value = state.config.ante;
$("#cfg-stack").value = state.config.stack;
$("#cfg-payouts").value = (state.config.payouts || []).join(",");
state.gridMode = "hero";
tuneIterations();
renderAll();
refreshLearn();
