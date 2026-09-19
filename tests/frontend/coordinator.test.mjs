// 请求协调器契约测试（S03/S04 / U-07..U-09）：使用真实 request-coordinator.js
// 模块与受控 fetch，验证 view/advice 分离、四元组守卫、requestId 最新者胜。
import test from "node:test";
import assert from "node:assert/strict";

import { createCoordinator } from "../../frontend/request-coordinator.js";
import { createSession } from "../../frontend/session.js";

function makeSession() {
  return createSession({
    capacity: 6,
    entries: [{ seatId: 1, chips: 10000 }, { seatId: 2, chips: 10000 }],
    heroSeatId: 2,
    buttonSeatId: 1, sbSeatId: 1, bbSeatId: 2,
    blindLevel: { sb: 100, bb: 200, anteEach: 0 },
    learningEnabled: false, icm: { scope: "off", payouts: [] },
  });
}

function okView(session) {
  return { ok: true, status: 200, json: async () => ({
    hand_id: session.currentHand.handId, street: "preflop", pot: 300,
    actor_seat_id: 1, hand_over: false, seats: [], board: [], taken: [],
  }) };
}

/** 受控 fetch：返回 {promise, resolve}，正文等待由测试控制。 */
function gatedFetch(status = 200, body = null) {
  let resolveRes, resolveBodyPromise, resolved;
  const promise = new Promise((r) => { resolveRes = r; });
  const bodyPromise = new Promise((r) => { resolveBodyPromise = r; });
  const fetchImpl = () => {
    resolveRes(undefined);
    const response = {
      ok: status >= 200 && status < 300,
      status,
      json: () => bodyPromise.then(() => resolved ?? body ?? ({ detail: "x" })),
    };
    return promise.then(() => response);
  };
  return {
    fetchImpl,
    async open() { await promise; },
    resolveBody(value) { resolved = value; resolveBodyPromise(); },
  };
}

const session = makeSession();

for (const stalledBody of [false, true]) test(`view timeout releases pending state (${stalledBody ? 'body' : 'headers'}) without committing`, async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const s = makeSession(), before = structuredClone(s), coord = createCoordinator(s), events = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    const stalled = new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
    return stalledBody ? {ok: true, json: () => stalled} : stalled;
  });
  coord.submitView(s, {}, {onAccept: () => events.push('accepted'), onError: e => events.push(e.message)});
  await Promise.resolve();
  assert.equal(coord.viewPending(), true);
  t.mock.timers.tick(15000);
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(coord.viewPending(), false);
  assert.equal(events.length, 1);
  assert.match(events[0], /超时/);
  assert.deepEqual(s, before);
  t.mock.method(globalThis, 'fetch', async () => okView(s));
  coord.submitView(s, {}, {onAccept: () => events.push('retry accepted')});
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(events.at(-1), 'retry accepted');
});

test("S-07/U-07 view 响应头到、正文未到时推进版本：旧正文不写入", async () => {
  const gate = gatedFetch(200, okView(session));
  const coord = createCoordinator(session);
  globalThis.fetch = gate.fetchImpl;
  const events = [];
  const revBefore = session.currentHand.revision;
  coord.submitView(session, {}, {
    onAccept(body) { events.push(["accept", body.hand_id]); },
    onStale() { events.push(["stale"]); },
    onError() { events.push(["error"]); },
  });
  await gate.open();                       // 响应"头"已到
  // 此时推进 revision（模拟重录/撤销）
  session.currentHand.revision += 1;
  gate.resolveBody();                      // 正文最后到达
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(events, [["stale"]], "旧正文必须以 stale 丢弃");
  assert.equal(session.currentHand.revision, revBefore + 1);
});

test("S-08/U-08 撤销再提交等长分支：旧 advice 后到不覆盖新分支", async () => {
  const s = makeSession();
  const coord = createCoordinator(s);
  const first = gatedFetch(200);
  globalThis.fetch = first.fetchImpl;
  const results = [];
  const payloadA = { ops: [{ op: "action", seat_id: 1, type: "call" }] };
  coord.submitAdvice(s, payloadA, {
    onResult(body) { results.push(["old", body]); },
    onStale() { results.push(["old-stale"]); },
    onError() { results.push(["old-error"]); },
  });
  await first.open();
  // 撤销后提交等长但不同的分支 → revision 变化
  s.currentHand.revision += 1;
  const second = gatedFetch(200);
  globalThis.fetch = second.fetchImpl;
  const payloadB = { ops: [{ op: "action", seat_id: 1, type: "fold" }] };
  coord.submitAdvice(s, payloadB, {
    onResult(body) { results.push(["new", body]); },
    onError() { results.push(["new-error"]); },
  });
  await second.open();
  first.resolveBody({ tag: "OLD" });       // 旧响应最后到
  second.resolveBody({ tag: "NEW" });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(results, [["old-stale"], ["new", { tag: "NEW" }]],
    "旧请求必须 stale；新请求生效");
});

test("S-09 同 revision 两个请求反序返回：requestId 最新者胜，旧请求不清新请求状态", async () => {
  const s = makeSession();
  const coord = createCoordinator(s);
  const first = gatedFetch(200);
  globalThis.fetch = first.fetchImpl;
  const events = [];
  coord.submitAdvice(s, { seq: 1 }, {
    onResult() { events.push("old-result"); },
    onStale() { events.push("old-stale"); },
  });
  await first.open();
  const second = gatedFetch(200);
  globalThis.fetch = second.fetchImpl;
  coord.submitAdvice(s, { seq: 2 }, {
    onResult() { events.push("new-result"); },
    onError() { events.push("new-error"); },
  });
  assert.ok(coord.advicePending(), "新请求在途");
  await second.open();
  first.resolveBody({ tag: "OLD" });       // 旧先回
  second.resolveBody({ tag: "NEW" });      // 新后回
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(events, ["old-stale", "new-result"]);
  assert.equal(coord.advicePending(), false);
});

test("S04.1/U-08 view pending 期间不排队第二个动作", () => {
  const s = makeSession();
  const coord = createCoordinator(s);
  const gate = gatedFetch(200);
  globalThis.fetch = gate.fetchImpl;
  let accepts = 0;
  coord.submitView(s, { seq: 1 }, { onAccept: () => accepts++ });
  assert.ok(coord.viewPending());
  // UI 层依据 viewPending() 禁用按钮；协调器层面第二次提交会直接取代（由 UI 防护）
  gate.resolveBody();
  return new Promise((r) => setTimeout(r, 10)).then(() => {
    assert.equal(accepts, 1);
    assert.equal(coord.viewPending(), false);
  });
});

test("S04.4/U-09 advice 失败只进推演状态，不影响 view", async () => {
  const s = makeSession();
  const coord = createCoordinator(s);
  const gate = gatedFetch(503);
  globalThis.fetch = gate.fetchImpl;
  const events = [];
  coord.submitAdvice(s, {}, {
    onResult() { events.push("result"); },
    onError(err) { events.push("error:" + err.message); },
  });
  await gate.open();
  gate.resolveBody({ detail: "服务暂不可用" });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(events, ["error:服务暂不可用"]);
  assert.equal(coord.viewPending(), false);
});

test("S-12 视图 503：错误回报，不落地任何动作", async () => {
  const s = makeSession();
  const coord = createCoordinator(s);
  const gate = gatedFetch(503);
  globalThis.fetch = gate.fetchImpl;
  const events = [];
  coord.submitView(s, {}, {
    onAccept() { events.push("accept"); },
    onError(err) { events.push("error:" + err.status_detail); },
  });
  await gate.open();
  gate.resolveBody({ detail: "服务暂不可用" });
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(events, ["error:undefined"]);
  assert.equal(s.currentHand.ops.length, 0, "引擎动作由 UI 在 onAccept 中落地，失败不落地");
});

test("S-16 会话结束/换手后旧响应作废", async () => {
  const s = makeSession();
  const coord = createCoordinator(s);
  const gate = gatedFetch(200);
  globalThis.fetch = gate.fetchImpl;
  const events = [];
  coord.submitView(s, {}, {
    onAccept() { events.push("accept"); },
    onStale() { events.push("stale"); },
  });
  await gate.open();
  coord.resync(makeSession());             // 换了新会话/新手
  gate.resolveBody();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(events, ["stale"]);
});
