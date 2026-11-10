"""Process-local, shared UTC daily budget for actual LLM request attempts."""
from datetime import datetime, timezone
from threading import Lock


class DailyLLMBudget:
    def __init__(self, limit=500, *, clock=None):
        if limit < 0:
            raise ValueError("DAILY_LLM_CALL_LIMIT must be nonnegative")
        self.limit = limit
        self.clock = clock or (lambda: datetime.now(timezone.utc).date())
        self._day = self.clock()
        self._used = 0
        self._lock = Lock()

    def _reset(self):
        day = self.clock()
        if day != self._day:
            self._day, self._used = day, 0

    def reserve(self):
        """Reserve before the network call; failed attempts also consume budget."""
        with self._lock:
            self._reset()
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    def snapshot(self):
        with self._lock:
            self._reset()
            return {"llm_calls_used": self._used, "llm_call_limit": self.limit,
                    "llm_degraded": self._used >= self.limit}

    @property
    def exhausted(self):
        return self.snapshot()["llm_degraded"]
