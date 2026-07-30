from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from mediasync_home.application.runtime_policy import RuntimePolicyStatus
from mediasync_home.domain.process_roles import ProcessRole


@dataclass(frozen=True)
class RuntimeStatus:
    application: str
    role: ProcessRole
    ready: bool
    mutations_enabled: bool
    protocol_version: int
    schema_version: int
    scope: str
    runtime_policy: RuntimePolicyStatus

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["role"] = self.role.value
        payload["runtime_policy"] = self.runtime_policy.to_dict()
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def startup_status(
    role: ProcessRole,
    *,
    runtime_policy: RuntimePolicyStatus | None = None,
) -> RuntimeStatus:
    return RuntimeStatus(
        application="MediaSync Home",
        role=role,
        ready=True,
        mutations_enabled=False,
        protocol_version=1,
        schema_version=1,
        scope="0B_NON_MUTATING_LOCAL_PREVIEW",
        runtime_policy=runtime_policy or RuntimePolicyStatus.not_evaluated(),
    )


def local_writable_status(
    role: ProcessRole,
    *,
    runtime_policy: RuntimePolicyStatus | None = None,
) -> RuntimeStatus:
    return RuntimeStatus(
        application="MediaSync Home",
        role=role,
        ready=True,
        mutations_enabled=True,
        protocol_version=1,
        schema_version=1,
        scope="0B_LOCAL_MUTATION_PREVIEW",
        runtime_policy=runtime_policy or RuntimePolicyStatus.not_evaluated(),
    )
