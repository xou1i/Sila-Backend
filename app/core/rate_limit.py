"""In-memory fixed-window rate limiter used as a FastAPI dependency (DECISIONS D-13)."""

import math
import threading
import time

from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode

_WINDOWS = {"second": 1, "minute": 60, "hour": 3600}


def parse_rate(rate: str) -> tuple[int, int]:
    count, _, unit = rate.partition("/")
    return int(count), _WINDOWS[unit.strip()]


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window: int) -> int | None:
        """Record a hit. Returns None if allowed, else seconds until the window resets."""
        now = time.monotonic()
        with self._lock:
            count, started = self._hits.get((bucket, key), (0, now))
            if now - started >= window:
                count, started = 0, now
            if count >= limit:
                return max(1, math.ceil(window - (now - started)))
            self._hits[(bucket, key)] = (count + 1, started)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(bucket: str):
    """Dependency factory; the limit is read from settings `rate_limit_<bucket>` at call time."""

    def dependency(request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit, window = parse_rate(getattr(settings, f"rate_limit_{bucket}"))
        client = request.client.host if request.client else "unknown"
        retry_after = limiter.hit(bucket, client, limit, window)
        if retry_after is not None:
            raise AppError(ErrorCode.RATE_LIMITED, headers={"Retry-After": str(retry_after)})

    return Depends(dependency)
