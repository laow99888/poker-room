// 牌库点击守卫的行为规范（对应 bug：手牌进行中点牌库会重开一手）。
// 规则（用户口径）：
//   1. 底牌未选齐且不在选公共牌模式 → 点击是选底牌；
//   2. 底牌已选满（无论牌局进行到哪）且不在选公共牌模式 → 点击必须被忽略；
//   3. 选公共牌模式下点击走选牌分支，与选底牌无关。
import test from "node:test";
import assert from "node:assert/strict";

import "../../frontend/logic.js";
const { heroPickAllowed } = globalThis.AppLogic;

test("底牌未选齐时允许点选底牌", () => {
  assert.equal(heroPickAllowed({ gridMode: null, heroCards: [null, null] }), true);
  assert.equal(heroPickAllowed({ gridMode: null, heroCards: ["As", null] }), true);
});

test("底牌已选满后点击必须被忽略（手牌进行中不得重开）", () => {
  assert.equal(heroPickAllowed({ gridMode: null, heroCards: ["As", "Ks"] }), false);
});

test("选公共牌模式下不走选底牌分支", () => {
  assert.equal(heroPickAllowed({ gridMode: "board", heroCards: ["As", null] }), false);
});

// 焦点区域规范：引导用户当前该看哪个模块。
// 规则（用户口径）：刷新后先在设置区选牌 → 选好后操作台高亮 →
// 该发/选公共牌时牌库高亮；手牌结束回操作台（再来一手）。
import "../../frontend/logic.js";
const { nextZone } = globalThis.AppLogic;

const VIEW = (over, actor) => ({ hand_over: over, actor });

test("底牌未选齐或无视图 → 设置区", () => {
  assert.equal(nextZone({ heroCards: [null, null], view: null, gridMode: null }), "setup");
  assert.equal(nextZone({ heroCards: ["As", null], view: null, gridMode: null }), "setup");
  assert.equal(nextZone({ heroCards: ["As", "Ks"], view: null, gridMode: null }), "setup");
});

test("该发公共牌或正在选公共牌 → 牌库", () => {
  assert.equal(nextZone({ heroCards: ["As", "Ks"], gridMode: "board",
                          view: { hand_over: false, actor: null } }), "deck");
  assert.equal(nextZone({ heroCards: ["As", "Ks"], gridMode: null,
                          view: { hand_over: false, actor: null } }), "deck");
});

test("行动阶段与手牌结束 → 操作台", () => {
  assert.equal(nextZone({ heroCards: ["As", "Ks"], gridMode: null,
                          view: { hand_over: false, actor: "BTN" } }), "console");
  assert.equal(nextZone({ heroCards: ["As", "Ks"], gridMode: null,
                          view: { hand_over: true, actor: null } }), "console");
});

// 10：失败回滚规则——只有"校验失败"(400) 才允许撤销已录操作；
// 服务/网络故障（5xx 等）必须保留历史，否则连删三部合法动作。
import "../../frontend/logic.js";
const { shouldRollbackOn, sameHand, handKey, playerId } = globalThis.AppLogic;

test("只有 400 触发撤销自愈，5xx/网络失败保留历史", () => {
  assert.equal(shouldRollbackOn(400), true);
  assert.equal(shouldRollbackOn(503), false);
  assert.equal(shouldRollbackOn(500), false);
  assert.equal(shouldRollbackOn(undefined), false);
});

// 09/25：建议响应归属校验——必须走真实调用契约 sameHand(handKey(a), handKey(b))。
// 旧测试手工拼 {opsLen, handKey} 对象掩盖了"handKey 返回 string 而比较读属性"
// 的契约错配（任何两个响应都被判为同一手牌）。
test("sameHand 判定响应是否仍属于当前牌局（真实 handKey 契约）", () => {
  const key = (cards, pos, opsLen) =>
    handKey({ heroCards: cards, heroPos: pos, ops: Array(opsLen).fill({}) });
  assert.equal(sameHand(undefined, key(["As", "Ks"], "BTN", 0)), false);
  assert.equal(
    sameHand(key(["As", "Ks"], "BTN", 3), key(["As", "Ks"], "BTN", 3)), true);
  assert.equal(
    sameHand(key(["As", "Ks"], "BTN", 3), key(["As", "Ks"], "BTN", 4)), false);
  assert.equal(
    sameHand(key(["As", "Ks"], "BTN", 3), key(["Qd", "Qc"], "BTN", 3)), false);
  assert.equal(
    sameHand(key(["As", "Ks"], "BTN", 3), key(["As", "Ks"], "CO", 3)), false);
});

// 27：匿名用户 ID——持久、稳定、不可用时回退 local
test("playerId 持久化并在无存储时回退", () => {
  const mem = new Map();
  const store = { getItem: (k) => (mem.has(k) ? mem.get(k) : null),
                  setItem: (k, v) => mem.set(k, v) };
  const a = playerId(store);
  assert.ok(a && typeof a === "string");
  assert.equal(playerId(store), a);        // 同一存储返回同一 ID
  assert.equal(playerId(null), "local");
});

// 14：每手新手牌唯一标识——内容相同的两手独立牌局靠它区分
import "../../frontend/logic.js";
const { newHandId, escapeHtml } = globalThis.AppLogic;

test("newHandId 生成非空且互不相同的标识", () => {
  const a = newHandId(), b = newHandId();
  assert.ok(a && typeof a === "string");
  assert.notEqual(a, b);
});

// 15：对手代号等用户自由输入必须转义后才能进 innerHTML
test("escapeHtml 中和 HTML 特殊字符", () => {
  assert.equal(escapeHtml('<img src=x onerror="alert(1)">'),
               "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(escapeHtml(`'&`), "&#39;&amp;");
  assert.equal(escapeHtml("老张"), "老张");       // 普通名字不受影响
});
