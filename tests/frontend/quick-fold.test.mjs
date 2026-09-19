import test from 'node:test';
import assert from 'node:assert/strict';
import * as S from '../../frontend/session.js';
import {validateSchema} from '../../frontend/session-storage.js';

function create(capacity = 6) {
  return S.createSession({capacity, entries: Array.from({length: capacity}, (_, i) => ({seatId: i + 1, chips: 10000})),
    heroSeatId: 1, buttonSeatId: capacity, sbSeatId: 1, bbSeatId: 2,
    blindLevel: {sb: 100, bb: 200, anteEach: 25}, learningEnabled: true});
}
const preview = (s, chips) => s.currentHand.context.participants.map(p => ({seat_id: p.seat_id,
  suggested_chips: p.occupant_id === s.heroOccupantId ? chips : null,
  source: p.occupant_id === s.heroOccupantId ? 'verified' : 'unknown'}));

for (const capacity of [6, 8, 9]) test(`${capacity} seats: a full orbit of quick folds preserves identities and estimated balances`, () => {
  let s = create(capacity), expected = 10000;
  const hero = s.heroOccupantId;
  for (let i = 0; i < capacity; i++) {
    expected -= 25 + (s.positions.smallBlindSeatId === 1 ? 100 : s.positions.bigBlindSeatId === 1 ? 200 : 0);
    S.closeQuickFold(s, preview(s, expected));
    assert.equal(S.validateSettlement(s, s.settlementDraft).ok, true);
    const tx = S.beginCommit(s), next = S.completeCommit(s, tx);
    assert.deepEqual(S.completeCommit(next, tx), next, 'retry must not charge twice');
    s = JSON.parse(JSON.stringify(next));
    assert.equal(validateSchema(s).ok, true);
    assert.equal(s.handNumber, i + 2);
    assert.equal(s.heroOccupantId, hero);
    assert.equal(s.occupants[hero].confirmedChips, expected);
    assert.equal(s.occupants[hero].chipsEstimated, false);
    for (const p of s.currentHand.context.participants.filter(p => p.occupant_id !== hero)) {
      assert.equal(p.starting_chips, 10000);
      assert.equal(s.occupants[p.occupant_id].chipsEstimated, true);
    }
    assert.equal(s.recentHands.at(-1).quickFold, true);
    assert.equal(s.recentHands.at(-1).estimatedOccupantIds.length, capacity - 1);
    assert.deepEqual(s.learningJobs, {});
  }
  assert.equal(s.positions.smallBlindSeatId, 1);
  assert.equal(s.positions.bigBlindSeatId, 2);
});

test('individual correction clears only its estimate; all-exact balances still require a difference explanation', () => {
  const s = create();
  S.closeQuickFold(s, preview(s, 9875));
  S.editSettlementRow(s.settlementDraft, 2, 12000);
  assert.equal(S.validateSettlement(s, s.settlementDraft).ok, true);
  const next = S.completeCommit(s, S.beginCommit(s));
  const player2 = next.seats[1].occupantId;
  assert.equal(next.occupants[player2].chipsEstimated, false);
  assert.equal(next.occupants[player2].confirmedChips, 12000);
  for (const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft, row.seatId, row.finalChips);
  assert.equal(S.validateSettlement(s, s.settlementDraft).ok, false);
});

test('manual closure can estimate opponents but never invent the hero result', () => {
  const s = create();
  S.manualCloseHand(s, 'missing showdown');
  S.useEstimatedBalances(s);
  assert.equal(s.settlementDraft.rows[0].finalChips, null);
  assert.equal(S.validateSettlement(s, s.settlementDraft).ok, false);
  S.editSettlementRow(s.settlementDraft, 1, 13000);
  assert.equal(S.validateSettlement(s, s.settlementDraft).ok, true);
});

test('estimated initial chips stay estimated even when the fold deduction is verified', () => {
  const s = create();
  s.occupants[s.heroOccupantId].chipsEstimated = true;
  S.closeQuickFold(s, preview(s, 9875));
  assert.equal(s.settlementDraft.rows[0].source, 'estimated');
  const next = S.completeCommit(s, S.beginCommit(s));
  assert.equal(next.occupants[next.heroOccupantId].chipsEstimated, true);
});

test('calibration changes only the current starting snapshot; histories and hand identity survive', () => {
  let s = create();
  S.closeQuickFold(s, preview(s, 9875));
  s = S.completeCommit(s, S.beginCommit(s));
  const histories = structuredClone(s.recentHands), id = s.currentHand.handId;
  S.calibrateStartingChips(s, 2, 12000);
  assert.equal(s.currentHand.context.participants[1].starting_chips, 12000);
  assert.equal(s.occupants[s.seats[1].occupantId].chipsEstimated, false);
  assert.deepEqual(s.recentHands, histories);
  assert.equal(s.currentHand.handId, id);
  for (const amount of [0, -1, 0.5, NaN, Number.MAX_SAFE_INTEGER]) assert.throws(() => S.calibrateStartingChips(s, 2, amount));
  S.setHeroCards(s, ['As', 'Ad']);
  assert.throws(() => S.calibrateStartingChips(s, 2, 10000));
});

test('quick records keep position calibration and pending edits; cancelling or replaying clears quick-fold metadata', () => {
  const s = create();
  s.positions = {buttonSeatId: 6, smallBlindSeatId: null, bigBlindSeatId: 1};
  S.closeQuickFold(s, preview(s, 9775));
  assert.throws(() => S.completeCommit(s, S.beginCommit(s)), /核对下一手/);
  S.cancelSettlement(s);
  assert.equal(s.currentHand.quickFold, undefined);
  S.closeQuickFold(s, preview(s, 9775));
  S.replayHand(s);
  assert.equal(s.currentHand.quickFold, undefined);
});

test('unknown or all-in hero cannot produce a quick-fold record', () => {
  const s = create(), before = structuredClone(s);
  for (const amount of [null, 0, -1, 1.5]) assert.throws(() => S.closeQuickFold(s, preview(s, amount)));
  assert.deepEqual(s, before);
});

test('complete action records with estimated balances also stay out of learning', () => {
  const s = create();
  s.phase = 'playing';
  S.enterSettling(s, s.currentHand.context.participants.map(p => ({seat_id: p.seat_id,
    suggested_chips: p.seat_id === 2 ? null : 10000, source: p.seat_id === 2 ? 'unknown' : 'verified'})));
  S.useEstimatedBalances(s);
  const next = S.completeCommit(s, S.beginCommit(s));
  assert.equal(next.recentHands[0].recordQuality, 'complete');
  assert.deepEqual(next.recentHands[0].estimatedOccupantIds, [s.seats[1].occupantId]);
  assert.deepEqual(next.learningJobs, {});
});
