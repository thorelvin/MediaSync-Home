from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from mediasync_home.application.job_creation import SealedStandardBackupJob


class JobEndpointRole(str, Enum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"


class EndpointRegistrationState(str, Enum):
    REGISTRATION_PENDING = "REGISTRATION_PENDING"
    READ_ONLY_READY = "READ_ONLY_READY"
    WRITABLE_READY = "WRITABLE_READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class EndpointIds:
    endpoint_id: str
    endpoint_revision_id: str


@dataclass(frozen=True, slots=True)
class StandardBackupJobEndpointBinding:
    job_id: str
    job_revision_id: str
    role: JobEndpointRole
    ordinal: int
    endpoint_id: str
    endpoint_revision_id: str
    display_name: str
    root_uri: str
    registration_state: EndpointRegistrationState

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "role": self.role.value,
            "ordinal": self.ordinal,
            "endpoint_id": self.endpoint_id,
            "endpoint_revision_id": self.endpoint_revision_id,
            "display_name": self.display_name,
            "root_uri": self.root_uri,
            "registration_state": self.registration_state.value,
        }


@dataclass(frozen=True, slots=True)
class StandardBackupJobEndpointSet:
    job_id: str
    job_revision_id: str
    source: StandardBackupJobEndpointBinding
    targets: tuple[StandardBackupJobEndpointBinding, ...]

    @property
    def all_bindings(self) -> tuple[StandardBackupJobEndpointBinding, ...]:
        return (self.source, *self.targets)

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "job_revision_id": self.job_revision_id,
            "source": self.source.to_dict(),
            "targets": [target.to_dict() for target in self.targets],
        }


class EndpointIdFactory(Protocol):
    def new_endpoint_ids(self) -> EndpointIds: ...


class StandardBackupJobEndpointRegistrar(Protocol):
    def register_standard_backup_job_endpoints(
        self,
        job: SealedStandardBackupJob,
    ) -> StandardBackupJobEndpointSet: ...

    def load_standard_backup_job_endpoint_set(
        self,
        *,
        job_id: str,
        job_revision_id: str,
    ) -> StandardBackupJobEndpointSet | None: ...
