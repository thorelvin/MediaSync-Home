from __future__ import annotations

from mediasync_home.application.host_lifecycle import EngineHostShutdownCoordinator


def test_shutdown_coordinator_admits_idle_host_once() -> None:
    coordinator = EngineHostShutdownCoordinator(idle_blockers=lambda: ())

    first = coordinator.request_shutdown()
    second = coordinator.request_shutdown()

    assert first.requested is True
    assert first.already_requested is False
    assert second.requested is True
    assert second.already_requested is True
    assert coordinator.shutdown_requested is True
    assert coordinator.try_begin_maintenance() is False


def test_shutdown_coordinator_rejects_active_maintenance_without_latching() -> None:
    coordinator = EngineHostShutdownCoordinator(idle_blockers=lambda: ())
    assert coordinator.try_begin_maintenance() is True

    blocked = coordinator.request_shutdown()
    coordinator.end_maintenance()
    admitted = coordinator.request_shutdown()

    assert blocked.requested is False
    assert blocked.blockers == ("ENGINE_HOST_MAINTENANCE_ACTIVE",)
    assert admitted.requested is True


def test_shutdown_coordinator_rejects_durable_activity() -> None:
    coordinator = EngineHostShutdownCoordinator(
        idle_blockers=lambda: (
            "ENGINE_HOST_ACTIVE_RUNS",
            "ENGINE_HOST_ACTIVE_RESOURCE_LEASES",
        )
    )

    decision = coordinator.request_shutdown()

    assert decision.requested is False
    assert decision.blockers == (
        "ENGINE_HOST_ACTIVE_RUNS",
        "ENGINE_HOST_ACTIVE_RESOURCE_LEASES",
    )
    assert coordinator.shutdown_requested is False


def test_shutdown_coordinator_fails_closed_when_idle_probe_fails() -> None:
    def fail() -> tuple[str, ...]:
        raise OSError("unreadable")

    coordinator = EngineHostShutdownCoordinator(idle_blockers=fail)

    decision = coordinator.request_shutdown()

    assert decision.requested is False
    assert decision.blockers == ("ENGINE_HOST_IDLE_CHECK_FAILED",)
