// Scripted playback: reveals each line word by word (as live partials), then emits it
// as a final turn through the same ingest path the microphone uses.

export type AutopilotLine = { text: string };

export type AutopilotCallbacks = {
  onPartial: (index: number, text: string) => void;
  onFinal: (index: number) => void;
  /** Step mode: playback paused before line `index`, waiting for next(). */
  onWaiting?: (index: number) => void;
  onDone: () => void;
};

export type AutopilotOptions = {
  wpm: number;
  gapMs: [number, number];
  speed?: number;
  stepMode?: boolean;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

export type Autopilot = {
  start: () => void;
  pause: () => void;
  /** Step mode: play the next line. Mid-line: finish the current line immediately. */
  next: () => void;
  stop: () => void;
  setSpeed: (speed: number) => void;
  setStepMode: (stepMode: boolean) => void;
  getIndex: () => number;
};

export function createAutopilot(
  lines: AutopilotLine[],
  startIndex: number,
  callbacks: AutopilotCallbacks,
  options: AutopilotOptions
): Autopilot {
  const setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = options.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  const random = options.random ?? Math.random;
  let speed = options.speed ?? 1;
  let stepMode = options.stepMode ?? false;
  let index = startIndex;
  let wordIdx = 0;
  let timer: unknown = null;
  let running = false;

  const words = (i: number) => lines[i].text.split(/\s+/).filter(Boolean);
  const wordMs = () => 60_000 / (options.wpm * speed);
  const gapMs = () => (options.gapMs[0] + random() * (options.gapMs[1] - options.gapMs[0])) / speed;

  function clear() {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }

  function schedule(ms: number) {
    clear();
    timer = setTimer(tick, ms);
  }

  function finish() {
    running = false;
    clear();
    callbacks.onDone();
  }

  function tick() {
    timer = null;
    if (!running) return;
    if (index >= lines.length) return finish();
    const w = words(index);
    wordIdx += 1;
    if (wordIdx < w.length) {
      callbacks.onPartial(index, w.slice(0, wordIdx).join(" "));
      schedule(wordMs());
      return;
    }
    callbacks.onFinal(index);
    index += 1;
    wordIdx = 0;
    if (index >= lines.length) return finish();
    if (stepMode) {
      running = false;
      callbacks.onWaiting?.(index);
      return;
    }
    schedule(gapMs());
  }

  return {
    start() {
      if (running || index >= lines.length) return;
      running = true;
      schedule(120);
    },
    pause() {
      running = false;
      clear();
    },
    next() {
      if (index >= lines.length) return;
      if (running && wordIdx > 0) {
        wordIdx = words(index).length - 1;
        schedule(0);
        return;
      }
      running = true;
      schedule(0);
    },
    stop() {
      running = false;
      clear();
    },
    setSpeed(next) {
      speed = next;
    },
    setStepMode(next) {
      stepMode = next;
    },
    getIndex: () => index,
  };
}
