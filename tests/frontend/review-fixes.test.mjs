import test from 'node:test';
import assert from 'node:assert/strict';
import * as S from '../../frontend/session.js';
import * as Store from '../../frontend/session-storage.js';
import {createCoordinator} from '../../frontend/request-coordinator.js';

function session() {
  return S.createSession({capacity: 6, entries: [1,2,3,4,5,6].map(seatId => ({seatId, chips: 10000})),
    heroSeatId: 1, buttonSeatId: 6, sbSeatId: 1, bbSeatId: 2,
    blindLevel: {sb: 100, bb: 200, anteEach: 0}, icm: {scope: 'off', payouts: []}});
}

test('81: end is a settlement transaction; a pending draft cannot be discarded', () => {
  const s = session();
  S.manualCloseHand(s, '现场结束');
  S.editSettlementRow(s.settlementDraft, 1, 9000);
  S.useEstimatedBalances(s);
  const before = structuredClone(s);
  assert.throws(() => S.endSession(s), /阶段/);
  assert.deepEqual(s, before);
  const tx = S.beginCommit(s, {endSession: true});
  const ended = S.completeCommit(s, tx);
  assert.deepEqual(s, before, '保存成功前不能修改正式状态');
  assert.equal(ended.phase, 'ended');
  assert.equal(ended.occupants[ended.heroOccupantId].confirmedChips, 9000);
  assert.equal(ended.recentHands.length, 1);
  assert.equal(ended.completedHands, 1);
  assert.equal(ended.currentHand, null);
  assert(Store.validateSchema(ended).ok);
  assert.deepEqual(S.completeCommit(ended, tx), ended, '重试幂等');
});

test('81: playing/partially selected hand must be reconciled before end', () => {
  const s = session();
  S.setHeroCards(s, ['As', null]);
  assert.throws(() => S.endSession(s), /核对余额/);
  s.phase = 'playing';
  assert.throws(() => S.endSession(s), /阶段/);
});

test('83: ending unused hand counts zero; cumulative count survives history truncation and old saves', () => {
  const empty = session();
  S.endSession(empty);
  assert.equal(S.completedHandsOf(empty), 0);
  let s = session();
  for (let i = 0; i < 105; i++) {
    s.phase = 'playing';
    S.enterSettling(s, s.currentHand.context.participants.map(p => ({seat_id: p.seat_id, source: 'verified', suggested_chips: 10000})));
    s = S.completeCommit(s, S.beginCommit(s));
  }
  assert.equal(s.handNumber, 106);
  assert.equal(s.recentHands.length, 100);
  assert.equal(S.completedHandsOf(s), 105);
  delete s.completedHands;
  assert.equal(S.completedHandsOf(s), 105, '旧存档迁移不依赖窗口长度');
  S.endSession(s);
  assert.equal(S.completedHandsOf(s), 105);
});

for (const body of [false, true]) test(`88: advice deadline releases busy and allows retry (${body ? 'body' : 'headers'})`, async t => {
  t.mock.timers.enable({apis: ['setTimeout']});
  const s = session(), c = createCoordinator(s), events = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    const stalled = new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
    return body ? {ok: true, json: () => stalled} : stalled;
  });
  c.submitAdvice(s, {}, {onError: e => events.push(e.message), onDone: () => events.push('done')});
  await Promise.resolve();
  assert(c.advicePending());
  t.mock.timers.tick(120000);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(c.advicePending(), false);
  assert.match(events[0], /超时/);
  assert.equal(events[1], 'done');
  t.mock.method(globalThis, 'fetch', async () => ({ok: true, json: async () => ({tag: 'retry'})}));
  c.submitAdvice(s, {}, {onResult: x => events.push(x.tag)});
  for (let i = 0; i < 10; i++) await Promise.resolve();
  assert.equal(events.at(-1), 'retry');
});
