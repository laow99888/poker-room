import test from 'node:test';
import assert from 'node:assert/strict';
import * as S from '../../frontend/session.js';
import * as Store from '../../frontend/session-storage.js';
import {createCoordinator} from '../../frontend/request-coordinator.js';

function session() {
  const s = S.createSession({capacity:6, entries:[1,2,3,4,5,6].map(seatId => ({seatId,chips:10000})),
    heroSeatId:1, buttonSeatId:6, sbSeatId:1, bbSeatId:2,
    blindLevel:{sb:100,bb:200,anteEach:0}, icm:{scope:'off',payouts:[]}});
  S.manualCloseHand(s,'现场核对');
  for (const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft,row.seatId,10000);
  return s;
}

test('46 网络拒绝与非JSON响应释放锁，可继续录入；旧请求不清新请求', async () => {
  const s = session(), coord = createCoordinator(s), errors = [];
  const original = globalThis.fetch;
  try {
    for (const submit of ['submitView','submitAdvice']) {
      for (const failure of [async () => {throw Error('offline');}, async () => ({json:async () => {throw Error('invalid JSON');}})]) {
        globalThis.fetch = failure;
        coord[submit](s, {}, {onError:e => errors.push(e.message)});
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(coord.viewPending(),false);
        assert.equal(coord.advicePending(),false);
      }
    }
    assert.deepEqual(errors,['offline','invalid JSON','offline','invalid JSON']);
  } finally { globalThis.fetch = original; }
});

test('48 提交后草稿变化、取消或重录都阻止旧事务', () => {
  for (const mutate of [s => S.editSettlementRow(s.settlementDraft,1,9000),
    s => S.cancelSettlement(s), s => S.replayHand(s),
    s => {s.nextHandDraft.blindLevel.bb = 400;}]) {
    const s = session(), tx = S.beginCommit(s);
    mutate(s);
    assert.throws(() => S.completeCommit(s,tx,null), {code:'stale_commit'});
    assert.equal(s.handNumber,1);
  }
});

test('50 连续移动按候选名单验证，不能覆盖已移动的玩家', () => {
  const s = session(), d = s.nextHandDraft;
  S.applyRosterEdit(s,d,{type:'leave',occupantId:s.seats[3].occupantId,reason:'table_transfer'});
  S.applyRosterEdit(s,d,{type:'move',occupantId:s.seats[4].occupantId,targetSeatId:4});
  assert.throws(() => S.applyRosterEdit(s,d,{type:'move',occupantId:s.seats[5].occupantId,targetSeatId:4}),{code:'seat_occupied'});
  S.applyRosterEdit(s,d,{type:'move',occupantId:s.seats[5].occupantId,targetSeatId:5});
  const candidate = S.previewRoster(s,d.rosterEdits);
  assert.equal(candidate.seats[3].occupantId,s.seats[4].occupantId);
  assert.equal(candidate.seats[4].occupantId,s.seats[5].occupantId);
  assert.equal(candidate.seats[5].occupantId,null);
});

test('59 零筹码排除后再建下一手；英雄淘汰直接结束且不要求位置', () => {
  const s = session();
  S.editSettlementRow(s.settlementDraft,3,0);
  S.editSettlementRow(s.settlementDraft,4,20000);
  assert.throws(() => S.completeCommit(s,S.beginCommit(s),null),{code:'positions_required'});
  S.applyDraftPositions(s.nextHandDraft,{...S.previewNextPositions(s),confirmed:true});
  const next = S.completeCommit(s,S.beginCommit(s),null);
  assert.equal(next.currentHand.context.participants.length,5);
  assert(next.currentHand.context.participants.every(p => p.starting_chips > 0));
  const h = session();
  S.editSettlementRow(h.settlementDraft,1,0);
  S.editSettlementRow(h.settlementDraft,2,20000);
  assert.equal(S.completeCommit(h,S.beginCommit(h),null).phase,'ended');
});

test('47/49/51 存储失败不覆盖旧数据，外部变化拒写，所有草稿可恢复', () => {
  const original = globalThis.localStorage;
  let raw = null, fail = false;
  globalThis.localStorage = {getItem:() => raw, setItem:(_,value) => {if(fail) throw Error('quota'); raw = value;}};
  try {
    const s = session();
    s.settlementDraft.adjustmentReason = '手前余额修正';
    s.settlementDraft.differenceAccepted = true;
    s.nextHandDraft.blindLevel.bb = 400;
    assert(Store.saveSession(s,null).ok);
    const first = raw;
    assert.deepEqual(Store.loadSession().session,s);
    fail = true;
    s.handNumber = 2;
    assert.equal(Store.saveSession(s,first).ok,false);
    assert.equal(raw,first);
    fail = false;
    raw = first + ' ';
    assert.equal(Store.saveSession(s,first).error,'conflict');
    assert.equal(raw,first + ' ');
    raw = '{"schemaVersion":100}';
    assert.equal(Store.loadSession().status,'future');
    assert.equal(raw,'{"schemaVersion":100}');
  } finally { globalThis.localStorage = original; }
});

test('P14/A13 空庄位须再次校准，转桌暂停ICM，下一手代号不改当前快照', () => {
  const s = session();
  s.icm = {scope:'final_table',payouts:[500,300],rosterConfirmed:true};
  s.nextHandDraft = S.emptyNextHandDraft(s);
  const id = s.seats[5].occupantId;
  S.applyRosterEdit(s,s.nextHandDraft,{type:'leave',occupantId:id,reason:'table_transfer'});
  assert.equal(s.nextHandDraft.icm.scope,'off');
  s.nextHandDraft.learningEnabled = true;
  s.nextHandDraft.profileNames = {[s.heroOccupantId]:'我的代号'};
  S.applyDraftPositions(s.nextHandDraft,{buttonSeatId:6,smallBlindSeatId:1,bigBlindSeatId:2,confirmed:true});
  const next = S.completeCommit(s,S.beginCommit(s),null);
  assert.deepEqual(s.currentHand.context.profile_names,{});
  assert.equal(next.currentHand.context.profile_names[s.heroOccupantId],'我的代号');
  S.manualCloseHand(next,'现场结束');
  for(const row of next.settlementDraft.rows) S.editSettlementRow(next.settlementDraft,row.seatId,10000);
  assert.throws(() => S.completeCommit(next,S.beginCommit(next),null),{code:'positions_required'});
});
