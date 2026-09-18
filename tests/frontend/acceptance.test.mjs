import test from 'node:test';
import assert from 'node:assert/strict';
import * as S from '../../frontend/session.js';
import * as Store from '../../frontend/session-storage.js';

function create(learningEnabled = false) {
  return S.createSession({capacity:6, entries:[1,2,3,4,5,6].map(seatId => ({seatId,chips:10000})),
    heroSeatId:5, buttonSeatId:6, sbSeatId:1, bbSeatId:2,
    blindLevel:{sb:100,bb:200,anteEach:0}, learningEnabled, icm:{scope:'off',payouts:[]}});
}

test('L09: 105 committed hands retain 100 summaries, every pending job, current hand and last transaction', () => {
  let s = create(true);
  const handIds = [];
  for (let i = 1; i <= 105; i++) {
    S.setHeroCards(s,['As','Ad']);
    s.phase='playing'; // The UI sets this only after a successful view response.
    handIds.push(s.currentHand.handId);
    S.enterSettling(s, s.currentHand.context.participants.map(p => ({seat_id:p.seat_id,source:'verified',suggested_chips:10000})));
    const tx = S.beginCommit(s);
    s = S.completeCommit(s,tx);
    S.markLearningJob(s,tx.sourceHandId,'failed','offline');
    assert.equal(s.handNumber,i+1);
    assert.deepEqual(S.completeCommit(s,tx),s);
  }
  assert.equal(s.recentHands.length,100);
  assert.equal(s.recentHands[0].handId,handIds[5]);
  assert.equal(Object.keys(s.learningJobs).length,105);
  assert.equal(s.learningJobs[handIds[0]].status,'failed');
  assert.equal(s.lastCommit.sourceHandId,handIds[104]);
  assert.equal(s.lastCommit.nextHandId,s.currentHand.handId);
  assert(Store.validateSchema(s).ok);
});

test('C02/C03: 10001 chips survive display conversion; decimal BB rounding is exact', () => {
  assert.equal(S.chipsToBBText(10001,200),'50.0');
  assert.equal(S.chipsFromBB(String(10001/200),200).chips,10001);
  assert.equal(S.chipsFromBB('0.333',300).chips,100);
  assert.equal(S.chipsFromBB('0.335',300).chips,101);
});

test('L05: malformed nested persisted data is rejected without throwing or replacing raw text', () => {
  const saved = globalThis.localStorage;
  try {
    for(const mutate of [
      s => {s.currentHand.context.participants=[null];},
      s => {s.currentHand.ops=[null];},
      s => {s.recentHands=[null];},
      s => {s.learningJobs={broken:null};},
      s => {S.manualCloseHand(s,'manual');s.settlementDraft.rows=[null];},
      s => {S.manualCloseHand(s,'manual');s.nextHandDraft.rosterEdits=[null];},
      s => {s.positions=null;},
    ]) {
      const s=create();mutate(s);
      const raw=JSON.stringify(s);
      globalThis.localStorage={getItem:()=>raw,setItem:()=>assert.fail('must not overwrite')};
      assert.equal(Store.loadSession().status,'corrupt',raw);
    }
  } finally {globalThis.localStorage=saved;}
});

test('A12/S10/S11: three-player ICM snapshot survives elimination; next hand uses new two-player payouts', () => {
  let s=S.createSession({capacity:6,entries:[1,2,3].map(seatId=>({seatId,chips:10000})),heroSeatId:3,
    buttonSeatId:3,sbSeatId:1,bbSeatId:2,blindLevel:{sb:100,bb:200,anteEach:0},
    learningEnabled:true,icm:{scope:'final_table',payouts:[500,300,200],rosterConfirmed:true}});
  const context=structuredClone(s.currentHand.context);
  S.manualCloseHand(s,'manual');
  for(const [seat,chips] of [[1,0],[2,15000],[3,15000]]) S.editSettlementRow(s.settlementDraft,seat,chips);
  S.setDraftIcm(s.nextHandDraft,{scope:'final_table',payouts:[500,300],rosterConfirmed:true});
  S.setDraftLearning(s.nextHandDraft,false);
  S.applyDraftPositions(s.nextHandDraft,{...S.previewNextPositions(s),confirmed:true});
  assert.deepEqual(s.currentHand.context,context);
  s=S.completeCommit(s,S.beginCommit(s));
  assert.deepEqual(s.currentHand.context.icm.payouts,[500,300]);
  assert.equal(s.currentHand.context.participants.length,2);
  assert.equal(s.currentHand.context.learning_enabled,false);
  assert.deepEqual(s.recentHands[0].context.icm.payouts,[500,300,200]);
});

test('P14: repeated missing small blind requires renewed position confirmation', () => {
  let s=S.createSession({capacity:6,entries:[2,3,4,5,6].map(seatId=>({seatId,chips:10000})),heroSeatId:5,
    buttonSeatId:6,sbSeatId:null,bbSeatId:2,blindLevel:{sb:100,bb:200,anteEach:0},icm:{scope:'off',payouts:[]}});
  for(let i=0;i<2;i++) {
    S.manualCloseHand(s,'manual');for(const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft,row.seatId,10000);
    assert.throws(()=>S.completeCommit(s,S.beginCommit(s)),{code:'positions_required'});
    S.applyDraftPositions(s.nextHandDraft,{buttonSeatId:6,smallBlindSeatId:null,bigBlindSeatId:2,confirmed:true});
    s=S.completeCommit(s,S.beginCommit(s));
    assert.equal(s.positions.smallBlindSeatId,null);
  }
});
