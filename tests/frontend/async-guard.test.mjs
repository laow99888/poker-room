// 36/40 异步守卫的行为级验证：不是读代码猜行为，而是把 app.js 里真实的
// refreshCore 函数源码提取出来，在受控 fetch 下驱动三个竞态场景——
//   A. 等长动作分支：撤销 fold 改 call（ops 同长、handKey 相同），旧 advice 后到；
//   B. view 响应头已到、等待 JSON 正文期间重开（handId/revision 变化）；
//   C. record 延迟失败响应在重开后到达，不得把旧手错误写入新一手。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../frontend/app.js", import.meta.url), "utf8");
const start = src.indexOf("async function refreshCore");
assert.ok(start > 0, "refreshCore 未找到");
let depth = 0, end = start;
for (let i = start; i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}") { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnSrc = src.slice(start, end);

function buildEnv(state, fetchImpl) {
  const logs = [];
  const scope = {
    state,
    AppLogic: { shouldRollbackOn: (s) => s === 400 },
    payload: () => ({}),
    statsHeaders: () => ({ "X-Player-Id": "test" }),
    renderAll: () => logs.push("renderAll"),
    renderAdvice: () => logs.push("renderAdvice"),
    showError: (m) => logs.push("showError:" + m),
    refreshLearn: () => logs.push("refreshLearn"),
    fetch: fetchImpl,
  };
  const keys = Object.keys(scope);
  const run = new Function(...keys, fnSrc + "\nreturn refreshCore;")(
    ...keys.map((k) => scope[k]));
  return { run, logs };
}

const tick = () => new Promise((r) => setTimeout(r, 10));
const okJson = (data) => ({ ok: true, status: 200, json: async () => data });

const baseState = () => ({
  handId: "h1", revision: 5, ops: [], view: null, advice: null,
  busy: true, error: null, recorded: true, pending: null, unconfirmed: false,
  heroPos: "BTN", heroCards: ["As", "Ks"],
  config: { player_count: 6, sb: 100, bb: 200, ante: 0 },
});

test("36-A 等长动作分支：revision 变化后旧 advice 不得写回", async () => {
  const state = baseState();
  let release;
  const gate = new Promise((r) => { release = r; });
  const fetchImpl = async (url) => {
    if (url === "/api/hand/view") return okJson({ hand_over: false, actor: "BTN" });
    if (url === "/api/hand/advice") {
      return { ok: true, status: 200, json: async () => { await gate; return { advice: { tag: "OLD" } }; } };
    }
    throw new Error("unexpected " + url);
  };
  const { run } = buildEnv(state, fetchImpl);
  const p = run();
  await tick();                       // view 已写入，advice 请求已发出
  assert.equal(state.advice, null);
  state.revision += 1;                // 撤销 fold 改 call（等长分支）
  release();
  await p;
  assert.equal(state.advice, null);   // 旧 advice 必须被丢弃
});

test("36-B 正文等待期间重开：旧 view 不写回且不再触动 busy", async () => {
  const state = baseState();
  let release;
  const gate = new Promise((r) => { release = r; });
  const fetchImpl = async () => ({
    ok: true, status: 200,
    json: async () => { await gate; return { hand_over: false, actor: "UTG" }; },
  });
  const { run } = buildEnv(state, fetchImpl);
  const p = run();
  await tick();
  // 重开（resetHandState 语义）：换手 + 推进版本 + 放行 busy
  state.handId = "h2"; state.revision += 1; state.busy = false;
  release();
  await p;
  assert.equal(state.view, null);     // 旧正文不得写回空手牌
  assert.equal(state.busy, false);    // busy 保持重置后的值
  assert.equal(state.advice, null);
});

test("40-C 旧手的 record 失败在重开后到达：不写入新手错误", async () => {
  const state = baseState();
  let release;
  const gate = new Promise((r) => { release = r; });
  const fetchImpl = async (url) => {
    if (url === "/api/hand/view") return okJson({ hand_over: true, actor: null });
    if (url === "/api/stats/record") {
      await gate;                     // 旧手的 record 响应延迟到达
      return { ok: false, status: 503, json: async () => ({}) };
    }
    throw new Error("unexpected " + url);
  };
  const { run, logs } = buildEnv(state, fetchImpl);
  const p = run();
  await tick();                       // view 已写入（hand_over），record 已发出
  state.recorded = false;             // 重开后的新手初始状态
  state.handId = "h2"; state.revision += 1; state.busy = false;
  release();
  await p;
  assert.equal(state.recorded, false);
  assert.ok(!logs.some((l) => l.startsWith("showError:学习数据")),
            "旧手记录失败不得写入新手: " + JSON.stringify(logs));
});

test("40-D 同一手内 record 失败：错误照常提示（守卫不吞错）", async () => {
  const state = { ...baseState(), recorded: false };
  const fetchImpl = async (url) => {
    if (url === "/api/hand/view") return okJson({ hand_over: true, actor: null });
    if (url === "/api/stats/record") return { ok: false, status: 503, json: async () => ({}) };
    throw new Error("unexpected " + url);
  };
  const { run, logs } = buildEnv(state, fetchImpl);
  await run();
  await tick();                       // record 为 fire-and-forget，等其回调入队执行
  assert.ok(logs.some((l) => l.startsWith("showError:学习数据记录失败")),
            JSON.stringify(logs));
});
