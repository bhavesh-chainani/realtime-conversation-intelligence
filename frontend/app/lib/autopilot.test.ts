import assert from "node:assert/strict";
import { test } from "node:test";

import { createAutopilot } from "./autopilot.ts";

function fakeClock() {
  const queue: Array<{ fn: () => void; id: number }> = [];
  let nextId = 1;
  return {
    setTimer: (fn: () => void) => {
      const id = nextId++;
      queue.push({ fn, id });
      return id;
    },
    clearTimer: (id: unknown) => {
      const i = queue.findIndex((t) => t.id === id);
      if (i >= 0) queue.splice(i, 1);
    },
    runAll(limit = 1000) {
      for (let n = 0; n < limit && queue.length; n += 1) queue.shift()!.fn();
    },
    pending: () => queue.length,
  };
}

const LINES = [{ text: "Hello there" }, { text: "Hi I am Sarah" }, { text: "Great" }];

test("plays every line in order, revealing words as partials", () => {
  const clock = fakeClock();
  const events: string[] = [];
  const ap = createAutopilot(
    LINES,
    0,
    {
      onPartial: (i, text) => events.push(`p${i}:${text}`),
      onFinal: (i) => events.push(`f${i}`),
      onDone: () => events.push("done"),
    },
    { wpm: 200, gapMs: [10, 20], setTimer: clock.setTimer, clearTimer: clock.clearTimer }
  );
  ap.start();
  clock.runAll();
  assert.deepEqual(events, [
    "p0:Hello",
    "f0",
    "p1:Hi",
    "p1:Hi I",
    "p1:Hi I am",
    "f1",
    "f2",
    "done",
  ]);
});

test("step mode waits after each line; next() finishes a line instantly", () => {
  const clock = fakeClock();
  const finals: number[] = [];
  const waits: number[] = [];
  const ap = createAutopilot(
    LINES,
    1,
    { onPartial: () => {}, onFinal: (i) => finals.push(i), onWaiting: (i) => waits.push(i), onDone: () => {} },
    { wpm: 200, gapMs: [10, 20], stepMode: true, setTimer: clock.setTimer, clearTimer: clock.clearTimer }
  );
  ap.start();
  clock.runAll(2); // reveal the first two words of line 1
  ap.next(); // finish line 1 now
  clock.runAll();
  assert.deepEqual(finals, [1]);
  assert.deepEqual(waits, [2]);
  assert.equal(clock.pending(), 0);

  ap.next();
  clock.runAll();
  assert.deepEqual(finals, [1, 2]);
  assert.equal(ap.getIndex(), 3);
});

test("stop cancels pending timers", () => {
  const clock = fakeClock();
  const ap = createAutopilot(
    LINES,
    0,
    { onPartial: () => {}, onFinal: () => assert.fail("should not finalize"), onDone: () => {} },
    { wpm: 200, gapMs: [10, 20], setTimer: clock.setTimer, clearTimer: clock.clearTimer }
  );
  ap.start();
  ap.stop();
  assert.equal(clock.pending(), 0);
});
