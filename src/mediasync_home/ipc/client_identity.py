from __future__ import annotations

from dataclasses import dataclass

from mediasync_home.domain.process_roles import ProcessRole
from mediasync_home.ipc.protocol import IpcReason


@dataclass(frozen=True)
class VerifiedClientIdentity:
    user_sid_hash: str
    session_id: int
    is_remote: bool
    transport: str


@dataclass(frozen=True)
class ClientAuthorizationPolicy:
    expected_user_sid_hash: str
    expected_session_id: int
    allowed_roles: frozenset[ProcessRole] = frozenset(
        {
            ProcessRole.GUI,
            ProcessRole.LAUNCHER,
            ProcessRole.TRIGGER_CLIENT,
        }
    )

    def reject_reason(
        self,
        role: ProcessRole,
        identity: VerifiedClientIdentity,
    ) -> IpcReason | None:
        if identity.is_remote:
            return IpcReason.REMOTE_CLIENT_REJECTED
        if identity.user_sid_hash != self.expected_user_sid_hash:
            return IpcReason.CLIENT_IDENTITY_MISMATCH
        if identity.session_id != self.expected_session_id:
            return IpcReason.CLIENT_IDENTITY_MISMATCH
        if role not in self.allowed_roles:
            return IpcReason.ROLE_NOT_ALLOWED
        return None
