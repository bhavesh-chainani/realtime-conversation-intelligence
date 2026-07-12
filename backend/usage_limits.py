from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import time


@dataclass
class UsageCheckResult:
    ok: bool
    reason: str | None = None


class InMemoryUsageLimiter:
    """Simple per-user limiter for MVP deployments.

    Note: In-memory state is per-process. For multi-instance AWS deployments,
    this should be replaced with a shared store (Redis/DynamoDB).
    """

    def __init__(self, per_minute_limit: int, daily_quota: int):
        self.per_minute_limit = max(1, per_minute_limit)
        self.daily_quota = max(1, daily_quota)
        self._lock = Lock()
        self._minute_windows: dict[str, deque[float]] = defaultdict(deque)
        self._daily_counts: dict[str, tuple[str, int]] = {}

    def check_and_record(self, user_key: str) -> UsageCheckResult:
        now = time()
        today = datetime.now(timezone.utc).date().isoformat()
        window_start = now - 60.0

        with self._lock:
            minute_window = self._minute_windows[user_key]
            while minute_window and minute_window[0] < window_start:
                minute_window.popleft()

            if len(minute_window) >= self.per_minute_limit:
                return UsageCheckResult(
                    ok=False,
                    reason=f"Rate limit exceeded: max {self.per_minute_limit} requests per minute",
                )

            stored_day, stored_count = self._daily_counts.get(user_key, (today, 0))
            if stored_day != today:
                stored_count = 0
                stored_day = today

            if stored_count >= self.daily_quota:
                return UsageCheckResult(
                    ok=False,
                    reason=f"Daily quota exceeded: max {self.daily_quota} requests per day",
                )

            minute_window.append(now)
            self._daily_counts[user_key] = (stored_day, stored_count + 1)
            return UsageCheckResult(ok=True)
