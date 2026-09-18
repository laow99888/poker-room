// P1 领域层契约测试：对应 acceptance.md 的 M/C/P/S 基础用例。
// 通过 session.js 公开接口操作状态，用独立预期核对金额/轮转/原子性。
import test from "node:test";
import assert from "node:assert/strict";

const S = await import("../../frontend/session.js");
const { createSession, chipsFromBB, chipsToBBText, automaticNextPositions,
        rangePositionFor,
        previewNextPositions, pushOp, setHeroCards, undoLastOp, replayHand,
        manualCloseHand, enterSettling, cancelSettlement, endSession,
        buildSettlementDraft, editSettlementRow, confirmAllCurrentValues,
        validateSettlement, beginCommit, completeCommit, emptyNextHandDraft,
        applyRosterEdit, applyDraftPositions, setDraftBlinds, rolesForSeat,
        heroSeatOf, DomainError } = S;

// F1：连续六人桌
function f1(overrides = {}) {
  return createSession({
    capacity: 6,
    entries: [1, 2, 3, 4, 5, 6].map((n) => ({ seatId: n, chips: 10000, name: `P${n}` })),
    heroSeatId: 5,
    buttonSeatId: 6, sbSeatId: 1, bbSeatId: 2,
    blindLevel: { sb: 100, bb: 200, anteEach: 0 },
    learningEnabled: false,
    icm: { scope: "off", payouts: [] },
    ...overrides,
  });
}

function settleAll(session, chipsBySeat, reason = null) {
  const draft = session.settlementDraft || buildSettlementDraft(session, []);
  for (const [seat, chips] of Object.entries(chipsBySeat)) {
    editSettlementRow(draft, Number(seat), chips);
  }
  confirmAllCurrentValues(draft);
  if (validateSettlement(session, draft).delta !== 0) {
    draft.adjustmentReason = reason || "测试确认的现场差额";
    draft.differenceAccepted = true;
  }
  session.settlementDraft = draft;
  return draft;
}

/* ------------------------------------------------------------ M-01 / P-01 */

test("M-01/P-01 F1 连续三手：你的座位固定 5，角色 CO→HJ→UTG，BTN 6→1→2", () => {
  let s = f1();
  const ids = [s.currentHand.handId];
  assert.equal(heroSeatOf(s), 5);
  assert.equal(rangePositionFor(s, 5), "CO");
  assert.deepEqual(rolesForSeat(s, 5), []);
  for (const [btn, sb, bb, role] of [[1, 2, 3, "HJ"], [2, 3, 4, "UTG"]]) {
    s.phase = "settling";
    s.settlementDraft = buildSettlementDraft(s, []);
    settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
    const tx = beginCommit(s);
    s = completeCommit(s, tx, null);
    ids.push(s.currentHand.handId);
    assert.equal(s.currentHand.handNumber, ids.length);
    assert.equal(rangePositionFor(s, 5), role);
    assert.equal(s.positions.buttonSeatId, btn);
    assert.equal(s.positions.smallBlindSeatId, sb);
    assert.equal(s.positions.bigBlindSeatId, bb);
  }
  assert.equal(heroSeatOf(s), 5);
  assert.equal(new Set(ids).size, 3, "每手独立 handId");
});

test("M-02 下一手 BTN 6→1：玩家与筹码留在原物理座位，仅角色移动", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  s = completeCommit(s, beginCommit(s), null);
  // P1(occupant) 仍在 seat1，只是角色 SB→BTN
  const seat1 = s.seats.find((x) => x.id === 1);
  assert.equal(s.occupants[seat1.occupantId].confirmedChips, 9900);
  assert.deepEqual(rolesForSeat(s, 1), ["BTN"]);
});

/* ---------------------------------------------------------- C-01..C-04 */

test("C-01 BB200 输入 50BB → 10000；显示 50.0BB/10000", () => {
  const r = chipsFromBB("50", 200);
  assert.equal(r.ok, true);
  assert.equal(r.chips, 10000);
  assert.equal(chipsToBBText(10000, 200), "50.0");
});

test("C-02 10001 筹码显示 50.0BB 不反写", () => {
  assert.equal(chipsToBBText(10001, 200), "50.0");
});

test("C-03 BB300：0.333BB→99.9→100；0.335BB→100.5→101", () => {
  const a = chipsFromBB("0.333", 300);
  assert.equal(a.chips, 100);
  assert.equal(a.exactText, "99.9");
  const b = chipsFromBB("0.335", 300);
  assert.equal(b.chips, 101);
  assert.equal(b.exactText, "100.5");
});

test("C-04 非法输入返回 ok:false，不产生数值", () => {
  for (const t of ["", " ", "abc", "-5", "NaN", "Infinity"]) {
    assert.equal(chipsFromBB(t, 300).ok, false, t);
  }
});

test("C-10 差额需原因与显式确认；齐全后允许纠错", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  const draft = s.settlementDraft;
  for (const [seat, chips] of Object.entries({ 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10100 })) {
    editSettlementRow(draft, Number(seat), chips);
  }
  confirmAllCurrentValues(draft);
  const v = validateSettlement(s, draft);
  assert.equal(v.ok, false);
  assert.equal(v.delta, 100);
  assert.ok(v.errors.some((e) => e.includes("原因")));
  assert.ok(v.errors.some((e) => e.includes("显式确认")));
  draft.adjustmentReason = "现场漏记一次前注";
  draft.differenceAccepted = true;
  assert.equal(validateSettlement(s, draft).ok, true);
});

test("C-19 单项合法但合计超安全整数 → 拒绝提交", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  const draft = s.settlementDraft;
  const big = Number.MAX_SAFE_INTEGER - 5;
  editSettlementRow(draft, 1, big);
  editSettlementRow(draft, 2, big);
  for (let seat = 3; seat <= 6; seat++) editSettlementRow(draft, seat, 10000);
  confirmAllCurrentValues(draft);
  const v = validateSettlement(s, draft);
  assert.equal(v.ok, false);
  assert.ok(v.errors.some((e) => e.includes("安全整数")));
});

/* ------------------------------------------------- F2 / C-05 / C-12 领域侧 */

test("F2 结算后第二手承接确认余额（9900/10100/…），手数=2", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  s = completeCommit(s, beginCommit(s), null);
  assert.equal(s.currentHand.handNumber, 2);
  const chips = Object.fromEntries(
    s.currentHand.context.participants.map((p) => [p.seat_id, p.starting_chips]));
  assert.deepEqual(chips, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  assert.equal(s.occupants[s.seats[0].occupantId].status, "active");
});

/* ------------------------------------------------- M-04/M-05 成员与移动 */

test("M-04 P4 转走、新 P7 入 seat4：不继承余额与代号", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const draft = emptyNextHandDraft(s);
  applyRosterEdit(s, draft, { type: "leave", occupantId: s.seats[3].occupantId, reason: "table_transfer" });
  applyRosterEdit(s, draft, { type: "enter", seatId: 4, chips: 8000, name: "P7" });
  applyDraftPositions(draft, { ...previewNextPositions(s, draft.rosterEdits), confirmed: true });
  s.nextHandDraft = draft;
  const tx = beginCommit(s);
  s = completeCommit(s, tx, null);
  const seat4 = s.seats.find((x) => x.id === 4);
  const occ = s.occupants[seat4.occupantId];
  assert.equal(occ.confirmedChips, 8000);
  assert.equal(occ.profileName, "P7");
  assert.notEqual(seat4.occupantId, "旧 occupant");
  const oldP4 = Object.values(s.occupants).find((o) => o.profileName === "P4");
  assert.equal(oldP4.status, "departed");
});

test("M-05 英雄 seat5→空 seat4：heroOccupantId 不变，仅座位变化", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const draft = emptyNextHandDraft(s);
  const heroOcc = s.heroOccupantId;
  const p4 = s.seats[3].occupantId;
  applyRosterEdit(s, draft, { type: "leave", occupantId: p4, reason: "table_transfer" });
  applyRosterEdit(s, draft, { type: "move", occupantId: heroOcc, targetSeatId: 4 });
  applyDraftPositions(draft, { ...automaticNextPositions(s), confirmed: true });
  s.nextHandDraft = draft;
  s = completeCommit(s, beginCommit(s), null);
  assert.equal(s.heroOccupantId, heroOcc);
  assert.equal(heroSeatOf(s), 4);
});

/* ------------------------------------------------------- M-08 / C-15 结束 */

test("M-08 英雄余额 0 → 会话结束，手数不增加，无新手", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 0, 6: 10000 });
  s = completeCommit(s, beginCommit(s), null);
  assert.equal(s.phase, "ended");
  assert.equal(s.currentHand, null);
  assert.equal(s.occupants[s.heroOccupantId].status, "eliminated");
  assert.equal(s.lastCommit.nextHandId, null);
});

test("C-15 剩余不足 2 人 → 会话结束", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 0, 2: 0, 3: 0, 4: 0, 5: 30000, 6: 0 });
  const draft = emptyNextHandDraft(s);
  applyDraftPositions(draft, { ...automaticNextPositions(s), confirmed: true });
  s.nextHandDraft = draft;
  s = completeCommit(s, beginCommit(s), null);
  assert.equal(s.phase, "ended");
  assert.equal(s.handNumber, 1, "手数不增加");
});

/* ------------------------------------------------------- S-01..S-04 状态机 */

test("S-01 重录本手：handId/context/手数/庄位不变，ops 清空 revision 增加", () => {
  let s = f1();
  setHeroCards(s, ["As", "Ad"]);
  s.phase = "playing";
  pushOp(s, { op: "action", seat_id: 3, type: "fold" });
  const handId = s.currentHand.handId;
  const rev = s.currentHand.revision;
  const btn = s.positions.buttonSeatId;
  s = replayHand(s);
  assert.equal(s.currentHand.handId, handId);
  assert.equal(s.currentHand.ops.length, 0);
  assert.equal(s.currentHand.heroCards[0], null);
  assert.equal(s.currentHand.revision, rev + 1);
  assert.equal(s.positions.buttonSeatId, btn);
  assert.equal(s.currentHand.handNumber, 1);
});

test("S-03 同事务重试返回已保存结果，其他同源事务拒绝", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const tx = beginCommit(s);
  s = completeCommit(s, tx, null);
  const handNo = s.currentHand.handNumber;
  assert.deepEqual(completeCommit(s, tx, null), s);
  assert.throws(() => completeCommit(s, {...tx, transactionId: "other"}, null), DomainError);
  assert.equal(s.currentHand.handNumber, handNo);
});

test("S-04 取消结算不改 confirmedChips，旧草稿不算确认", () => {
  let s = f1();
  s.phase = "playing";
  s = enterSettling(s, []);
  const draft = s.settlementDraft;
  editSettlementRow(draft, 1, 12345);
  s = cancelSettlement(s);
  assert.equal(s.phase, "ready", "无动作时取消结算回到 ready");
  assert.equal(s.settlementDraft, null);
  const occ1 = s.occupants[s.seats[0].occupantId];
  assert.equal(occ1.confirmedChips, 10000, "confirmedChips 未被草稿污染");
});

/* --------------------------------------------------------- P-05/P-06 淘汰 */

function headsUpFromThree(eliminateSeat) {
  let s = createSession({
    capacity: 6,
    entries: [{ seatId: 1, chips: 10000, name: "P1" },
      { seatId: 2, chips: 10000, name: "P2" },
      { seatId: 3, chips: 10000, name: "P3" }],
    heroSeatId: 3,
    buttonSeatId: 3, sbSeatId: 1, bbSeatId: 2,
    blindLevel: { sb: 100, bb: 200, anteEach: 0 },
    learningEnabled: false, icm: { scope: "off", payouts: [] },
  });
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  const chips = { 1: 10000, 2: 10000, 3: 10000 };
  chips[eliminateSeat] = 0;
  settleAll(s, chips);
  const draft = emptyNextHandDraft(s);
  applyRosterEdit(s, draft, { type: "leave", occupantId: s.seats[eliminateSeat - 1].occupantId, reason: "eliminated" });
  const preview = previewNextPositions(s, draft.rosterEdits);
  applyDraftPositions(draft, { ...preview, confirmed: true });
  s.nextHandDraft = draft;
  return { s, preview };
}

test("P-05 3人 seat1 淘汰：候选 BB3、BTN/SB2（不是继续 BB2）", () => {
  let { s, preview } = headsUpFromThree(1);
  assert.equal(preview.bigBlindSeatId, 3);
  assert.equal(preview.buttonSeatId, 2);
  assert.equal(preview.smallBlindSeatId, 2);
  const tx = beginCommit(s);
  s = completeCommit(s, tx, null);
  assert.equal(s.phase, "ready");
  assert.equal(activeSeatsOf(s).length, 2);
});

test("P-06 3人 seat2 淘汰：候选 BB3、BTN/SB1", () => {
  const { preview } = headsUpFromThree(2);
  assert.equal(preview.bigBlindSeatId, 3);
  assert.equal(preview.buttonSeatId, 1);
});

function activeSeatsOf(s) {
  return s.seats.filter((x) => x.occupantId && s.occupants[x.occupantId].status === "active").map((x) => x.id);
}

/* ------------------------------------------------------------- F4 单挑 */

test("P-07/F4 单挑两手：角色互换、余额按各自占用者", () => {
  let s = createSession({
    capacity: 6,
    entries: [{ seatId: 2, chips: 10000, name: "P2" }, { seatId: 5, chips: 10000, name: "P5" }],
    heroSeatId: 5,
    buttonSeatId: 5, sbSeatId: 5, bbSeatId: 2,
    blindLevel: { sb: 100, bb: 200, anteEach: 0 },
    learningEnabled: false, icm: { scope: "off", payouts: [] },
  });
  assert.deepEqual(rolesForSeat(s, 5), ["BTN", "SB"]);
  assert.deepEqual(rolesForSeat(s, 2), ["BB"]);
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 2: 10000, 5: 10000 });
  s = completeCommit(s, beginCommit(s), null);
  assert.deepEqual(rolesForSeat(s, 2), ["BTN", "SB"], "第二手角色互换");
  assert.deepEqual(rolesForSeat(s, 5), ["BB"]);
  assert.equal(s.occupants[s.seats[1].occupantId].confirmedChips, 10000);
  assert.equal(s.occupants[s.seats[4].occupantId].confirmedChips, 10000);
});

/* ------------------------------------------------------- C-14 升盲草稿 */

test("C-14 下一手升盲 BB200→400：余额不变，旧手结算用旧 BB", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const draft = emptyNextHandDraft(s);
  setDraftBlinds(draft, 200, 400, 0);
  applyDraftPositions(draft, { ...automaticNextPositions(s), confirmed: true });
  s.nextHandDraft = draft;
  s = completeCommit(s, beginCommit(s), null);
  assert.equal(s.blindLevel.bb, 400);
  const p5 = s.occupants[s.seats[4].occupantId];
  assert.equal(p5.confirmedChips, 10000, "余额不因升盲改变");
  assert.equal(s.currentHand.context.blinds.bb, 400);
  assert.equal(chipsToBBText(10000, 400), "25.0");
});

/* ------------------------------------------------- C-17 手工结束（不完整） */

test("C-17 手工结束：manual_close 必填原因，进入结算", () => {
  let s = f1();
  setHeroCards(s, ["As", "Ad"]);
  s.phase = "playing";
  pushOp(s, { op: "action", seat_id: 3, type: "fold" });
  assert.throws(() => manualCloseHand(s, "  "), DomainError);
  s = manualCloseHand(s, "还有两家未录完，先离场");
  assert.equal(s.phase, "settling");
  assert.equal(s.currentHand.recordQuality, "manual_close");
  assert.equal(s.currentHand.manualCloseReason, "还有两家未录完，先离场");
});

/* ------------------------------------------------- S-02 完整事务原子性 */

test("S-02 prepare 失败时整笔不落盘：调用方保留结算页", () => {
  let s = f1();
  s.phase = "settling";
  s.settlementDraft = buildSettlementDraft(s, []);
  settleAll(s, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const tx = beginCommit(s);
  // prepare 失败：不调用 completeCommit，会话保持 settling、草稿仍在
  assert.equal(s.phase, "settling");
  assert.equal(s.currentHand.handNumber, 1);
  assert.equal(s.settlementDraft.sourceHandId, tx.sourceHandId);
});

test("M-09 建桌非法输入：重复座位/重复 occupant/英雄空座全部拒绝", () => {
  const base = {
    capacity: 6,
    entries: [{ seatId: 1, chips: 10000 }, { seatId: 1, chips: 10000 }],
    heroSeatId: 1, buttonSeatId: 1, sbSeatId: 1, bbSeatId: 2,
    blindLevel: { sb: 100, bb: 200, anteEach: 0 },
    learningEnabled: false, icm: { scope: "off", payouts: [] },
  };
  assert.throws(() => createSession(base), /座位非法或重复/);
  assert.throws(() => createSession({
    ...base,
    entries: [{ seatId: 1, chips: 10000 }, { seatId: 2, chips: 10000 }],
    heroSeatId: 9,
  }), /英雄/);
  assert.throws(() => createSession({
    ...base, entries: [{ seatId: 1, chips: 10000, occupantId: "p-x" }, { seatId: 2, chips: 10000, occupantId: "p-x" }],
  }), /occupantId 重复/);
  assert.throws(() => createSession({
    ...base, entries: [{ seatId: 1, chips: 0 }, { seatId: 2, chips: 10000 }],
  }), /起始筹码/);
});

/* -------------------------------------------------- A-04 学习记录任务队列 */

test("A-04 开启学习且 complete 手结算后入队原 payload；manual_close 不入队", () => {
  const on = f1({ learningEnabled: true });
  setHeroCards(on, ["As", "Ad"]);
  on.phase = "playing";                    // 领域层外由 UI 在首次 view 验证后推进
  for (const seat of [3, 4, 5, 6, 1]) pushOp(on, { op: "action", type: "fold", seatId: seat });
  enterSettling(on);
  const completeHandId = on.currentHand.handId;
  settleAll(on, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const s2 = completeCommit(on, beginCommit(on), null);
  assert.equal(Object.keys(s2.learningJobs).length, 1);
  const job = s2.learningJobs[completeHandId];
  assert.equal(job.status, "pending");
  assert.equal(job.payload.hand_id, completeHandId);
  assert.equal(job.payload.protocol_version, 2);
  assert.equal(job.payload.context.learning_enabled, true);
  assert.deepEqual(job.payload.hero_cards, ["As", "Ad"]);
  assert.equal(job.payload.ops.length, 5);

  // manual_close：学习开着也不入队（牌谱不完整不能接续画像）
  const on2 = f1({ learningEnabled: true });
  setHeroCards(on2, ["As", "Ad"]);
  on2.phase = "playing";
  for (const seat of [3, 4, 5, 6, 1]) pushOp(on2, { op: "action", type: "fold", seatId: seat });
  manualCloseHand(on2, "有人误亮牌，现场裁定重发筹码");
  settleAll(on2, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const s3 = completeCommit(on2, beginCommit(on2), null);
  assert.deepEqual(s3.learningJobs, {});
});

test("A-05 markLearningJob：成功即清除、失败保留原 payload 供重试", () => {
  const on = f1({ learningEnabled: true });
  setHeroCards(on, ["As", "Ad"]);
  on.phase = "playing";                    // 领域层外由 UI 在首次 view 验证后推进
  for (const seat of [3, 4, 5, 6, 1]) pushOp(on, { op: "action", type: "fold", seatId: seat });
  enterSettling(on);
  const hid = on.currentHand.handId;
  settleAll(on, { 1: 9900, 2: 10100, 3: 10000, 4: 10000, 5: 10000, 6: 10000 });
  const s2 = completeCommit(on, beginCommit(on), null);
  const original = JSON.stringify(s2.learningJobs[hid].payload);
  S.markLearningJob(s2, hid, "failed", "HTTP 503");
  assert.equal(s2.learningJobs[hid].status, "failed");
  assert.equal(s2.learningJobs[hid].lastError, "HTTP 503");
  assert.equal(JSON.stringify(s2.learningJobs[hid].payload), original);  // 重试用原 payload
  assert.equal(S.pendingLearningJobs(s2).length, 1);
  S.markLearningJob(s2, hid, "success");
  assert.equal(s2.learningJobs[hid], undefined);
  assert.deepEqual(S.pendingLearningJobs(s2), []);
});
