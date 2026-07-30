from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic_ns


class SystemClock:
    def utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00",
            "Z",
        )

    def monotonic_ns(self) -> int:
        return monotonic_ns()
