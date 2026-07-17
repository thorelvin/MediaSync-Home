from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RuntimePolicyStatus:
    evaluated: bool
    compliant: bool
    elevated: bool | None
    controlled_current_directory: bool
    dll_search_policy: str
    handle_inheritance_policy: str
    reasons: tuple[str, ...]

    @classmethod
    def not_evaluated(cls) -> "RuntimePolicyStatus":
        return cls(
            evaluated=False,
            compliant=False,
            elevated=None,
            controlled_current_directory=False,
            dll_search_policy="NOT_EVALUATED",
            handle_inheritance_policy="NOT_EVALUATED",
            reasons=("RUNTIME_POLICY_NOT_EVALUATED",),
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def evaluate_runtime_policy(
    *,
    elevated: bool | None,
    controlled_current_directory: bool,
    dll_search_policy: str,
    handle_inheritance_policy: str,
) -> RuntimePolicyStatus:
    reasons: list[str] = []
    if elevated is None:
        reasons.append("ELEVATION_UNKNOWN")
    elif elevated:
        reasons.append("PROCESS_ELEVATED")
    if not controlled_current_directory:
        reasons.append("UNCONTROLLED_CURRENT_DIRECTORY")
    if dll_search_policy == "NOT_CONFIGURED":
        reasons.append("DLL_SEARCH_POLICY_NOT_CONFIGURED")
    if handle_inheritance_policy == "NOT_CONFIGURED":
        reasons.append("HANDLE_INHERITANCE_POLICY_NOT_CONFIGURED")

    return RuntimePolicyStatus(
        evaluated=True,
        compliant=not reasons,
        elevated=elevated,
        controlled_current_directory=controlled_current_directory,
        dll_search_policy=dll_search_policy,
        handle_inheritance_policy=handle_inheritance_policy,
        reasons=tuple(reasons),
    )
