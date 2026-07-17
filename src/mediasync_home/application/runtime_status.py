from __future__ import annotations

import json
from dataclasses import asdict, dataclass

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

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["role"] = self.role.value
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def startup_status(role: ProcessRole) -> RuntimeStatus:
    return RuntimeStatus(
        application="MediaSync Home",
        role=role,
        ready=True,
        mutations_enabled=False,
        protocol_version=1,
        schema_version=1,
        scope="0B_NON_MUTATING_LOCAL_PREVIEW",
    )
