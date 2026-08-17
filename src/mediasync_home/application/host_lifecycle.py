from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EngineHostShutdownDecision:
    requested: bool
    already_requested: bool = False
    blockers: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "already_requested": self.already_requested,
            "blockers": list(self.blockers),
        }


class EngineHostShutdownPort(Protocol):
    def request_shutdown(self) -> EngineHostShutdownDecision: ...


class EngineHostShutdownCoordinator:
    def __init__(self, *, idle_blockers: Callable[[], tuple[str, ...]]) -> None:
        self._idle_blockers = idle_blockers
        self._lock = Lock()
        self._maintenance_count = 0
        self._shutdown_requested = False

    @property
    def shutdown_requested(self) -> bool:
        with self._lock:
            return self._shutdown_requested

    def try_begin_maintenance(self) -> bool:
        with self._lock:
            if self._shutdown_requested:
                return False
            self._maintenance_count += 1
            return True

    def end_maintenance(self) -> None:
        with self._lock:
            if self._maintenance_count < 1:
                raise RuntimeError("ENGINE_HOST_MAINTENANCE_NOT_ACTIVE")
            self._maintenance_count -= 1

    def request_shutdown(self) -> EngineHostShutdownDecision:
        with self._lock:
            if self._shutdown_requested:
                return EngineHostShutdownDecision(
                    requested=True,
                    already_requested=True,
                )
            blockers: list[str] = []
            if self._maintenance_count:
                blockers.append("ENGINE_HOST_MAINTENANCE_ACTIVE")
            try:
                blockers.extend(self._idle_blockers())
            except Exception:
                blockers.append("ENGINE_HOST_IDLE_CHECK_FAILED")
            unique_blockers = tuple(dict.fromkeys(blockers))
            if unique_blockers:
                return EngineHostShutdownDecision(
                    requested=False,
                    blockers=unique_blockers,
                )
            self._shutdown_requested = True
            return EngineHostShutdownDecision(requested=True)
