import test from 'node:test';
import assert from 'node:assert/strict';
import * as S from '../../frontend/session.js';

function create({capacity = 6, seats = [1, 2, 3, 4], hero = seats[0], button = 4, sb = null, bb = 1} = {}) {
  return S.createSession({
    capacity, entries: seats.map(seatId => ({seatId, chips: 10000})),
    heroSeatId: hero, buttonSeatId: button, sbSeatId: sb, bbSeatId: bb,
    blindLevel: {sb: 100, bb: 200, anteEach: 25}, icm: {scope: 'off', payouts: []},
  });
}

function settle(s) {
  S.manualCloseHand(s, 'position regression');
  for (const row of s.settlementDraft.rows) S.editSettlementRow(s.settlementDraft, row.seatId, 10000);
  S.confirmAllCurrentValues(s.settlementDraft);
}

test('missing small blind: the generated candidate commits without manually repairing its positions', () => {
  const s = create();
  settle(s);
  const candidate = S.previewNextPositions(s);
  S.applyDraftPositions(s.nextHandDraft, {...candidate, confirmed: true});
  const next = S.completeCommit(s, S.beginCommit(s));
  assert.equal(next.handNumber, 2);
  assert.equal(next.positions.smallBlindSeatId, 1);
  assert.equal(next.positions.bigBlindSeatId, 2);
  assert.equal(next.positions.buttonSeatId, 4);
  assert.deepEqual(next.currentHand.context.participants.map(p => p.seat_id), [1, 2, 3, 4]);
});

for (const fixture of [
  {name: 'small blind leaves: retain the empty former small-blind seat as button', leave: [1], expected: [1, 2, 3]},
  {name: 'big blind leaves: allow a missing small blind', leave: [2], expected: [1, null, 3]},
  {name: 'both blinds leave: retain a legal dead button', leave: [1, 2], expected: [1, null, 3]},
  {name: 'button leaves: blinds still advance normally', leave: [6], expected: [1, 2, 3]},
]) {
  test(fixture.name, () => {
    const s = create({seats: [1, 2, 3, 4, 5, 6], hero: 5, button: 6, sb: 1, bb: 2});
    settle(s);
    for (const seat of fixture.leave) S.applyRosterEdit(s, s.nextHandDraft, {
      type: 'leave', occupantId: s.seats[seat - 1].occupantId, reason: 'table_transfer',
    });
    const p = S.previewNextPositions(s, s.nextHandDraft.rosterEdits);
    assert.deepEqual([p.buttonSeatId, p.smallBlindSeatId, p.bigBlindSeatId], fixture.expected);
    S.applyDraftPositions(s.nextHandDraft, {...p, confirmed: true});
    const next = S.completeCommit(s, S.beginCommit(s));
    assert.equal(next.handNumber, 2);
    assert.equal(next.currentHand.context.participants.length, 6 - fixture.leave.length);
  });
}

test('a new player between the old small and big blinds does not produce an illegal button', () => {
  const s = create({seats: [1, 3, 4, 5], button: 5, sb: 1, bb: 3});
  settle(s);
  S.applyRosterEdit(s, s.nextHandDraft, {type: 'enter', seatId: 2, chips: 10000});
  const p = S.previewNextPositions(s, s.nextHandDraft.rosterEdits);
  assert.deepEqual([p.buttonSeatId, p.smallBlindSeatId, p.bigBlindSeatId], [2, 3, 4]);
  S.applyDraftPositions(s.nextHandDraft, {...p, confirmed: true});
  assert.equal(S.completeCommit(s, S.beginCommit(s)).handNumber, 2);
});

test('all occupied-seat layouts and valid current blind arrangements produce legal next candidates', t => {
  let checked = 0;
  for (let capacity = 2; capacity <= 9; capacity++) {
    for (let mask = 1; mask < 2 ** capacity; mask++) {
      const seats = Array.from({length: capacity}, (_, i) => i + 1).filter(id => mask & (1 << (id - 1)));
      if (seats.length < 2) continue;
      for (let button = 1; button <= capacity; button++) {
        const after = seats.filter(id => id > button).concat(seats.filter(id => id <= button));
        const options = seats.length === 2
          ? (seats.includes(button) ? [[button, after[0]]] : [])
          : [[after[0], after[1]], ...(seats.length < capacity ? [[null, after[0]]] : [])];
        for (const [sb, bb] of options) {
          const s = create({capacity, seats, button, sb, bb});
          const p = S.previewNextPositions(s);
          const label = JSON.stringify({capacity, seats, button, sb, bb, candidate: p});
          const order = seats.filter(id => id > p.buttonSeatId).concat(seats.filter(id => id <= p.buttonSeatId));
          assert.equal(p.bigBlindSeatId, seats.find(id => id > bb) ?? seats[0], label);
          if (seats.length === 2) {
            assert.equal(p.smallBlindSeatId, p.buttonSeatId, label);
            assert.notEqual(p.buttonSeatId, p.bigBlindSeatId, label);
          } else {
            assert.equal(order[0], p.smallBlindSeatId, label);
            assert.equal(order[1], p.bigBlindSeatId, label);
          }
          S.normalizePositions(p, s.seats, s.occupants, 200);
          checked++;
        }
      }
    }
  }
  assert(checked > 10000, `checked ${checked} layouts`);
  t.diagnostic(`${checked} current position layouts checked`);
});

test('6/8/9 seats: every surviving roster and old big blind can confirm and start the next hand', t => {
  let checked = 0;
  for (const capacity of [6, 8, 9]) {
    const seats = Array.from({length: capacity}, (_, i) => i + 1);
    for (let mask = 1; mask < 2 ** capacity; mask++) {
      const remaining = seats.filter(id => mask & (1 << (id - 1)));
      if (remaining.length < 2) continue;
      for (const bb of seats) {
        const sb = (bb - 2 + capacity) % capacity + 1;
        const button = (bb - 3 + capacity) % capacity + 1;
        const s = create({capacity, seats, hero: remaining[0], button, sb, bb});
        settle(s);
        for (const id of seats) S.editSettlementRow(s.settlementDraft, id,
          id === remaining[0] ? 10000 * (capacity - remaining.length + 1) : remaining.includes(id) ? 10000 : 0);
        const p = S.previewNextPositions(s);
        const expectedBb = remaining.find(id => id > bb) ?? remaining[0];
        assert.equal(p.bigBlindSeatId, expectedBb);
        if (remaining.length === 2) assert.notEqual(p.bigBlindSeatId, p.buttonSeatId);
        S.applyDraftPositions(s.nextHandDraft, {...p, confirmed: true});
        const next = S.completeCommit(s, S.beginCommit(s));
        assert.equal(next.handNumber, 2);
        assert.deepEqual(next.currentHand.context.participants.map(v => v.seat_id), remaining);
        assert.equal(next.currentHand.context.participants.reduce((sum, v) => sum + v.starting_chips, 0), capacity * 10000);
        assert.deepEqual(next.currentHand.context.participants.map(v => v.occupant_id),
          remaining.map(id => s.seats[id - 1].occupantId));
        assert.equal(s.handNumber, 1, 'preview/commit must not mutate the source hand');
        checked++;
      }
    }
  }
  t.diagnostic(`${checked} elimination and heads-up transitions committed`);
});
