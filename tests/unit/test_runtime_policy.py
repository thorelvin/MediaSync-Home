from __future__ import annotations

from mediasync_home.application.runtime_policy import (
    RuntimePolicyStatus,
    evaluate_runtime_policy,
)
from mediasync_home.application.runtime_status import startup_status
from mediasync_home.domain.process_roles import ProcessRole


def test_runtime_policy_is_compliant_for_non_elevated_controlled_role() -> None:
    status = evaluate_runtime_policy(
        elevated=False,
        controlled_current_directory=True,
        dll_search_policy="LOCAL_DEV_NO_CHILD_PROCESS_DLL_SEARCH_SURFACE",
        handle_inheritance_policy="NO_CHILD_PROCESS_SPAWNED_BY_ROLE_RUNNER",
    )

    assert status.evaluated is True
    assert status.compliant is True
    assert status.reasons == ()


def test_runtime_policy_reports_elevation_and_uncontrolled_directory() -> None:
    status = evaluate_runtime_policy(
        elevated=True,
        controlled_current_directory=False,
        dll_search_policy="NOT_CONFIGURED",
        handle_inheritance_policy="NOT_CONFIGURED",
    )

    assert status.compliant is False
    assert status.reasons == (
        "PROCESS_ELEVATED",
        "UNCONTROLLED_CURRENT_DIRECTORY",
        "DLL_SEARCH_POLICY_NOT_CONFIGURED",
        "HANDLE_INHERITANCE_POLICY_NOT_CONFIGURED",
    )


def test_startup_status_serializes_runtime_policy() -> None:
    status = startup_status(
        ProcessRole.GUI,
        runtime_policy=RuntimePolicyStatus(
            evaluated=True,
            compliant=True,
            elevated=False,
            controlled_current_directory=True,
            dll_search_policy="LOCAL_DEV_NO_CHILD_PROCESS_DLL_SEARCH_SURFACE",
            handle_inheritance_policy="NO_CHILD_PROCESS_SPAWNED_BY_ROLE_RUNNER",
            reasons=(),
        ),
    ).to_dict()

    assert status["runtime_policy"] == {
        "compliant": True,
        "controlled_current_directory": True,
        "dll_search_policy": "LOCAL_DEV_NO_CHILD_PROCESS_DLL_SEARCH_SURFACE",
        "elevated": False,
        "evaluated": True,
        "handle_inheritance_policy": "NO_CHILD_PROCESS_SPAWNED_BY_ROLE_RUNNER",
        "reasons": [],
    }
