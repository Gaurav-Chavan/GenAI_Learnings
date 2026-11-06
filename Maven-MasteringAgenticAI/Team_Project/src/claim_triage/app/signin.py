"""Sign-in for the password-protected web app: one shared password, and a pause for everyone after too many wrong tries.

The state lives in this module, so it is shared by every visitor's session in the same server process. The password itself is never stored or logged here;
the entry file passes it in from the app's Secrets and this module only compares.
"""
from __future__ import annotations

import hmac
import math
import threading
import time
from collections import deque
from typing import Callable


class SignInGate:
    def __init__(self, max_failures: int = 8, window_seconds: int = 300, clock: Callable[[], float] = time.monotonic):
        self.max_failures, self.window, self.clock = max_failures, window_seconds, clock
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    def _forget_old(self, now: float) -> None:
        while self._failures and now - self._failures[0] >= self.window:
            self._failures.popleft()

    def _paused_for(self, now: float) -> int:
        self._forget_old(now)
        if len(self._failures) < self.max_failures:
            return 0
        return max(1, math.ceil(self._failures[0] + self.window - now))  # open again when the oldest wrong try has aged out

    def paused_for(self) -> int:
        """Seconds until sign-in is open again; 0 when it is open now."""
        with self._lock:
            return self._paused_for(self.clock())

    def attempt(self, given: str, expected: str) -> str:
        """'ok', 'wrong' or 'paused'. While paused, even the right password is refused and the try is not counted. An empty expected password never matches."""
        with self._lock:
            now = self.clock()
            if self._paused_for(now):
                return "paused"
            if expected and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
                return "ok"
            self._failures.append(now)
            return "wrong"

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


GATE = SignInGate()
