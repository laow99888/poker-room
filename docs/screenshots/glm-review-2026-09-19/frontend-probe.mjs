// Read-only business-code review probe. Run with:
// node docs/screenshots/glm-review-2026-09-19/frontend-probe.mjs
// Writes only the adjacent frontend-probe.json evidence artifact.
// Controller tests execute actual app.js functions in a Node VM with mocked
// DOM rendering, fetch, and storage. These are NOT browser interaction tests.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import * as S from "../../../frontend/session.js";
import { createCoordinator } from "../../../frontend/request-coordinator.js";

const results = [];
const turn = () => new Promise((resolve) => setTimeout(resolve, 30));

function makeSession(count = 6) {
  return S.createSession({
    capacity: 6,
    entries: Array.from({ length: count }, (_, i) => ({ seatId: i + 1, chips: 10000 })),
    heroSeatId: 5,
    buttonSeatId: count,
    sbSeatId: 1,
    bbSeatId: 2,
    blindLevel: { sb: 100, bb: 200, anteEach: 0 },
  });
}

function settlingSession(count = 6) {
  const session = makeSession(count);
  S.manualCloseHand(session, "review probe");
  for (const row of session.settlementDraft.rows) row.finalChips = 10000;
  return session;
}

// Load the production controller without imports or browser bootstrap. The
// imported domain functions remain real; rendering/network/storage are stubs.
function controllerHarness(saveResult = { ok: true }) {
  let source = fs.readFileSync(new URL("../../../frontend/app.js", import.meta.url), "utf8");
  const start = source.indexOf("const $ =");
  const end = source.lastIndexOf("if (document.readyState");
  assert.ok(start >= 0 && end > start, "app.js extraction anchors changed");
  source = source.slice(start, end);
  source += `
    renderAll = () => {};
    syncLearningJobs = () => {};
    setSaveLabel = () => {};
    setStatus = (message) => globalThis.statusMessage = message;
    showSettleError = (message) => globalThis.settlementError = message;
    globalThis.harness = { rt, commitSettlement, persist };
  `;
  const writes = [];
  const context = vm.createContext({
    ...S,
    Store: { saveSession(session) { writes.push(structuredClone(session)); return saveResult; } },
    document: { querySelector: () => ({}) },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  vm.runInContext(source, context, { filename: "production-app-controller.vm.js" });
  context.harness.rt.session = settlingSession();
  context.harness.rt.coord = { resync() {} };
  const area = { querySelector: () => ({ disabled: false }) };
  return { context, writes, area, ...context.harness };
}

// Network failure: real coordinator, rejected mocked fetch; no HTTP service.
{
  const originalFetch = globalThis.fetch;
  const unhandled = [];
  const onUnhandled = (error) => unhandled.push({ name: error.name, message: error.message });
  process.on("unhandledRejection", onUnhandled);
  try {
    globalThis.fetch = async () => { throw new Error("offline-probe"); };
    for (const channel of ["view", "advice"]) {
      const session = makeSession();
      const coordinator = createCoordinator(session);
      const errors = [];
      const before = unhandled.length;
      const method = channel === "view" ? "submitView" : "submitAdvice";
      coordinator[method](session, {}, { onError(error) { errors.push(error.message); } });
      await turn();
      const pending = channel === "view" ? coordinator.viewPending() : coordinator.advicePending();
      const rejection = unhandled.slice(before);
      assert.equal(pending, true);
      assert.deepEqual(errors, []);
      assert.equal(rejection[0]?.message, "currentSource is not defined");
      results.push({ id: `network-${channel}`, method: "real module + rejected fetch mock", pending, errorCallbacks: errors, unhandledRejections: rejection, defectReproduced: true });
    }
  } finally {
    globalThis.fetch = originalFetch;
    process.removeListener("unhandledRejection", onUnhandled);
  }
}

// Storage quota: real commit orchestration, failed save adapter.
{
  const h = controllerHarness({ ok: false, error: "quota:probe" });
  await h.commitSettlement(h.area);
  assert.equal(h.rt.session.phase, "ready");
  assert.equal(h.rt.session.handNumber, 2);
  assert.equal(h.rt.saveState, "error");
  results.push({ id: "save-failure-advances-hand", method: "production controller in Node VM; mocked prepare/save/render", phase: h.rt.session.phase, handNumber: h.rt.session.handNumber, saveState: h.rt.saveState, saveAttempts: h.writes.length, statusMessage: h.context.statusMessage, defectReproduced: true });
}

// Multi-tab conflict flag: verify the actual persistence entry point still writes.
{
  const h = controllerHarness();
  h.rt.conflict = true;
  h.persist();
  assert.equal(h.writes.length, 1);
  results.push({ id: "conflict-does-not-block-write", method: "production persist() in Node VM; conflict flag set as storage listener does", conflict: h.rt.conflict, saveAttempts: h.writes.length, defectReproduced: true });
}

// Stale prepare: re-record the source hand while commit awaits the response.
{
  const h = controllerHarness();
  let release;
  h.context.fetch = () => new Promise((resolve) => { release = resolve; });
  const old = h.rt.session;
  const revisionBefore = old.currentHand.revision;
  const commit = h.commitSettlement(h.area);
  assert.equal(typeof release, "function");
  S.replayHand(old);
  const revisionAfterReplay = old.currentHand.revision;
  assert.ok(revisionAfterReplay > revisionBefore);
  release({ ok: true, json: async () => ({}) });
  await commit;
  assert.equal(h.rt.session.handNumber, 2);
  assert.equal(h.rt.session.recentHands.length, 1);
  results.push({ id: "stale-commit-survives-replay", method: "production controller in Node VM; deferred prepare response + real replayHand", revisionBefore, revisionAfterReplay, resultingHandNumber: h.rt.session.handNumber, resultingPhase: h.rt.session.phase, completedHands: h.rt.session.recentHands.length, defectReproduced: true });
}

// Two moves to one vacant seat: real pure domain functions, no mocks.
{
  const session = settlingSession(5);
  const draft = S.emptyNextHandDraft(session);
  const player3 = session.seats[2].occupantId;
  const player4 = session.seats[3].occupantId;
  S.applyRosterEdit(session, draft, { type: "move", occupantId: player3, targetSeatId: 6 });
  S.applyRosterEdit(session, draft, { type: "move", occupantId: player4, targetSeatId: 6 });
  S.applyDraftPositions(draft, { buttonSeatId: 5, smallBlindSeatId: 6, bigBlindSeatId: 1, confirmed: true });
  session.nextHandDraft = draft;
  const next = S.completeCommit(session, S.beginCommit(session), null);
  const missing = Object.values(next.occupants).filter((occupant) => occupant.status === "active" && !next.seats.some((seat) => seat.occupantId === occupant.id));
  const sum = (s) => s.currentHand.context.participants.reduce((total, p) => total + p.starting_chips, 0);
  assert.equal(missing.length, 1);
  assert.equal(missing[0].id, player3);
  assert.equal(sum(session), 50000);
  assert.equal(sum(next), 40000);
  results.push({ id: "duplicate-move-loses-participant", method: "real session.js functions; no mocks", acceptedMoves: draft.rosterEdits.length, targetSeat: 6, activeOccupantsWithoutSeat: missing.length, chipsBefore: sum(session), chipsAfter: sum(next), defectReproduced: true });
}

const evidence = {
  generatedAt: new Date().toISOString(),
  node: process.version,
  scope: "Independent Node reproductions. VM controller probes are not browser tests. No server or real localStorage/statistics data accessed.",
  assertions: "Assertions intentionally confirm the reviewed defects, not product acceptance.",
  results,
};
const output = new URL("./frontend-probe.json", import.meta.url);
fs.writeFileSync(output, JSON.stringify(evidence, null, 2) + "\n", "utf8");
console.log(JSON.stringify(evidence, null, 2));
console.log(`Evidence written: ${fileURLToPath(output)}`);
