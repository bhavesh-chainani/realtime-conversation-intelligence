// When the call started and ended, for the header's call clock.
import { useCallback, useState } from "react";

export function useCallClock() {
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [endedAt, setEndedAt] = useState<number | null>(null);

  return {
    startedAt,
    endedAt,
    /** Start the clock, or carry on after a pause. */
    start: useCallback(() => {
      setStartedAt((prev) => prev ?? Date.now());
    }, []),
    end: useCallback(() => setEndedAt(Date.now()), []),
    reset: useCallback(() => {
      setStartedAt(null);
      setEndedAt(null);
    }, []),
  };
}
