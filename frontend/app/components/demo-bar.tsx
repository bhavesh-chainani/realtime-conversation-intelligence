import { useEffect, useState } from "react";

import type {
  AutopilotState,
  CacheStatus,
  DemoScenario,
  DemoScenarioSummary,
  InputMode,
  Preflight,
} from "../lib/types.ts";

type DemoBarProps = {
  scenarios: DemoScenarioSummary[];
  scenarioId: string;
  onScenarioChange: (id: string) => void;
  scenario: DemoScenario | null;
  inputMode: InputMode;
  onInputModeChange: (mode: InputMode) => void;
  isListening: boolean;
  autopilotState: AutopilotState;
  onStart: () => void;
  onStop: () => void;
  onNextLine: () => void;
  onReset: () => void;
  speed: number;
  onSpeedChange: (speed: number) => void;
  stepMode: boolean;
  onStepModeChange: (stepMode: boolean) => void;
  cursor: number;
  skippedLines: string[];
  cacheStatus: CacheStatus | null;
  isBuildingCache: boolean;
  onBuildCache: () => void;
  preflight: Preflight | null;
  isCheckingPreflight: boolean;
  onRunPreflight: () => void;
};

const PREFLIGHT_LABELS: Array<[keyof Omit<Preflight, "ok">, string]> = [
  ["llm", "LLM"],
  ["db", "DB"],
  ["stt", "STT"],
  ["cache", "Cache"],
  ["limits", "Limits"],
];

function preflightTitle(key: string, check: Preflight[keyof Omit<Preflight, "ok">]): string {
  const parts = [`${key}: ${check.ok ? "ok" : "check"}`];
  if (typeof check.ms === "number") parts.push(`${check.ms}ms`);
  if (check.error) parts.push(String(check.error));
  if (typeof check.model === "string") parts.push(check.model);
  if (typeof check.cases === "number") parts.push(`${check.cases} cases`);
  if (typeof check.steps === "number") parts.push(`${check.steps}/${check.total} steps${check.fresh ? "" : " (stale)"}`);
  return parts.join(" · ");
}

export function DemoBar({
  scenarios,
  scenarioId,
  onScenarioChange,
  scenario,
  inputMode,
  onInputModeChange,
  isListening,
  autopilotState,
  onStart,
  onStop,
  onNextLine,
  onReset,
  speed,
  onSpeedChange,
  stepMode,
  onStepModeChange,
  cursor,
  skippedLines,
  cacheStatus,
  isBuildingCache,
  onBuildCache,
  preflight,
  isCheckingPreflight,
  onRunPreflight,
}: DemoBarProps) {
  // Hidden by default: on a shared screen the audience would read the next line before it is said.
  const [showScript, setShowScript] = useState(false);
  useEffect(() => {
    try {
      setShowScript(localStorage.getItem("DEMO_SHOW_SCRIPT") === "1");
    } catch {}
  }, []);
  const toggleScript = () => {
    setShowScript((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("DEMO_SHOW_SCRIPT", next ? "1" : "0");
      } catch {}
      return next;
    });
  };

  const lines = scenario?.lines ?? [];
  const current = lines[cursor];
  const upcoming = lines[cursor + 1];
  const isRunning =
    inputMode === "autopilot"
      ? autopilotState === "running" || autopilotState === "waiting"
      : isListening;
  const staffName = scenario?.staff_name || "Staff";
  const customerName = scenario?.persona?.name || "Customer";
  const speakerName = (role: string) => (role === "staff" ? staffName : customerName);

  let cacheLabel = "No cache";
  let cacheTone = "neutral";
  if (isBuildingCache) cacheLabel = "Preparing…";
  else if (cacheStatus?.built) {
    cacheLabel = `Prepared ${cacheStatus.steps}/${cacheStatus.total}${cacheStatus.fresh ? "" : " · stale"}`;
    cacheTone = cacheStatus.fresh && cacheStatus.steps === cacheStatus.total ? "success" : "warning";
  }

  return (
    <section className="panel demo-bar" aria-label="Demo controls">
      <div className="demo-bar__controls">
        <div className="demo-bar__group">
          <label className="field-label" htmlFor="demo-scenario">
            Scenario
          </label>
          <select
            id="demo-scenario"
            className="field-input demo-bar__select"
            value={scenarioId}
            onChange={(e) => onScenarioChange(e.target.value)}
            disabled={isRunning}
          >
            <option value="">Free-form (no script)</option>
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </div>

        <div className="demo-bar__group">
          <span className="field-label">Input</span>
          <div className="role-toggle-group">
            <button
              type="button"
              className={`btn btn--toggle btn--sm${inputMode === "live" ? " btn--toggle-active" : ""}`}
              onClick={() => onInputModeChange("live")}
              aria-pressed={inputMode === "live"}
              disabled={isRunning}
            >
              Live mic
            </button>
            <button
              type="button"
              className={`btn btn--toggle btn--sm${inputMode === "autopilot" ? " btn--toggle-active" : ""}`}
              onClick={() => onInputModeChange("autopilot")}
              aria-pressed={inputMode === "autopilot"}
              disabled={isRunning || !scenario}
              title={scenario ? "Play the script without a microphone" : "Select a scenario first"}
            >
              Autopilot
            </button>
          </div>
        </div>

        <div className="demo-bar__group demo-bar__group--actions">
          {isRunning ? (
            <button type="button" className="btn btn--secondary btn--sm" onClick={onStop}>
              {inputMode === "autopilot" ? "Pause" : "Stop mic"}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={onStart}
              disabled={inputMode === "autopilot" && (!scenario || cursor >= lines.length)}
            >
              {inputMode === "autopilot"
                ? autopilotState === "paused"
                  ? "Resume"
                  : cursor > 0
                    ? "Continue from here"
                    : "Play script"
                : "Start mic"}
            </button>
          )}
          {inputMode === "autopilot" ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onNextLine}
              disabled={!scenario || cursor >= lines.length}
              title="Play / finish the next line (→ key)"
            >
              Next line →
            </button>
          ) : null}
          <button type="button" className="btn btn--ghost btn--sm" onClick={onReset}>
            Reset
          </button>
          {scenario ? (
            <button
              type="button"
              className={`btn btn--toggle btn--sm${showScript ? " btn--toggle-active" : ""}`}
              onClick={toggleScript}
              aria-pressed={showScript}
              title="Show the presenters' script (teleprompter). Matching runs either way."
            >
              {showScript ? "Hide script" : "Show script"}
            </button>
          ) : null}
        </div>

        {inputMode === "autopilot" ? (
          <div className="demo-bar__group">
            <span className="field-label">Pace</span>
            <div className="role-toggle-group">
              {[1, 1.5, 2].map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`btn btn--toggle btn--sm${speed === s ? " btn--toggle-active" : ""}`}
                  onClick={() => onSpeedChange(s)}
                  aria-pressed={speed === s}
                >
                  {s}×
                </button>
              ))}
              <button
                type="button"
                className={`btn btn--toggle btn--sm${stepMode ? " btn--toggle-active" : ""}`}
                onClick={() => onStepModeChange(!stepMode)}
                aria-pressed={stepMode}
                title="Pause after every line; press Next line (→) to continue"
              >
                Step
              </button>
            </div>
          </div>
        ) : null}

        <div className="demo-bar__group demo-bar__group--status">
          {scenario ? (
            <button
              type="button"
              className={`status-badge status-badge--${cacheTone} demo-bar__chip-btn`}
              onClick={onBuildCache}
              disabled={isBuildingCache || isRunning}
              title="Pre-compute suggestions for every customer line (instant fallback when the live call is slow). Click to rebuild."
            >
              {cacheLabel}
            </button>
          ) : null}
          <button
            type="button"
            className="preflight"
            onClick={onRunPreflight}
            disabled={isCheckingPreflight}
            title="Re-run preflight checks"
          >
            {PREFLIGHT_LABELS.map(([key, label]) => {
              const check = preflight?.[key];
              const tone = !check ? "pending" : check.ok ? "ok" : "bad";
              return (
                <span key={key} className="preflight__item" title={check ? preflightTitle(label, check) : label}>
                  <span className={`preflight__dot preflight__dot--${isCheckingPreflight ? "pending" : tone}`} />
                  {label}
                </span>
              );
            })}
          </button>
        </div>
      </div>

      {scenario && showScript ? (
        <div className="teleprompter" aria-live="polite">
          <div className="teleprompter__progress">
            {cursor >= lines.length ? "Script complete" : `Line ${cursor + 1} of ${lines.length}`}
            {skippedLines.length ? ` · skipped ${skippedLines.join(", ")}` : ""}
          </div>
          {current ? (
            <>
              <p className={`teleprompter__line teleprompter__line--${current.role}`}>
                <span className={`role-badge role-badge--${current.role}`}>
                  Next · {speakerName(current.role)}
                </span>
                {current.text}
              </p>
              {current.beat ? <div className="teleprompter__beat">Watch for: {current.beat.label}</div> : null}
              {upcoming ? (
                <p className="teleprompter__line teleprompter__line--upcoming">
                  <span className="teleprompter__who">{speakerName(upcoming.role)}:</span> {upcoming.text}
                </p>
              ) : null}
            </>
          ) : (
            <p className="teleprompter__line">That&apos;s the end of the script. Ad-lib a closing or press Reset.</p>
          )}
        </div>
      ) : null}
    </section>
  );
}
