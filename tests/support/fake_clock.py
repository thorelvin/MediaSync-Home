from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeClock:
    utc_now_value: str = "2026-07-31T00:00:00.000Z"
    monotonic_ns_value: int = 0

    def utc_now(self) -> str:
        return self.utc_now_value

    def monotonic_ns(self) -> int:
        return self.monotonic_ns_value

    def set_utc(self, value: str) -> None:
        self.utc_now_value = value

    def advance_monotonic_ms(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("FAKE_CLOCK_CANNOT_ADVANCE_NEGATIVE_DURATION")
        self.monotonic_ns_value += milliseconds * 1_000_000
