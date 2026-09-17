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
const { shouldRollbackOn, sameHand } = globalThis.AppLogic;

test("只有 400 触发撤销自愈，5xx/网络失败保留历史", () => {
  assert.equal(shouldRollbackOn(400), true);
  assert.equal(shouldRollbackOn(503), false);
  assert.equal(shouldRollbackOn(500), false);
  assert.equal(shouldRollbackOn(undefined), false);
});

// 09：建议响应归属校验——请求发出后牌局变了（撤销/重开/继续操作），旧响应必须丢弃
test("sameHand 判定响应是否仍属于当前牌局", () => {
  assert.equal(sameHand(null, { opsLen: 0 }), false);
  assert.equal(sameHand({ opsLen: 3, handKey: "AsKs|BTN" }, { opsLen: 3, handKey: "AsKs|BTN" }), true);
  assert.equal(sameHand({ opsLen: 3, handKey: "AsKs|BTN" }, { opsLen: 4, handKey: "AsKs|BTN" }), false);
  assert.equal(sameHand({ opsLen: 3, handKey: "AsKs|BTN" }, { opsLen: 3, handKey: "QdQc|BTN" }), false);
});
