from __future__ import annotations

from typing import Final, NoReturn


_MUTATION_PERMIT_ISSUER_TOKEN: Final[object] = object()


class MutationPermit:
    __slots__ = (
        "_endpoint_id",
        "_endpoint_revision_id",
        "_fencing_token",
        "_lease_id",
        "_owner_installation_id",
        "_ownership_epoch",
        "_resource_key",
        "_run_id",
        "_run_target_id",
    )

    def __new__(
        cls,
        issuer_token: object | None = None,
        **_metadata: object,
    ) -> "MutationPermit":
        if cls is MutationPermit and issuer_token is not _MUTATION_PERMIT_ISSUER_TOKEN:
            raise TypeError("MutationPermit instances are issued only by a live lease adapter")
        return object.__new__(cls)

    def __init__(
        self,
        issuer_token: object | None = None,
        *,
        lease_id: str | None = None,
        resource_key: str | None = None,
        owner_installation_id: str | None = None,
        ownership_epoch: int | None = None,
        fencing_token: int | None = None,
        run_id: str | None = None,
        run_target_id: str | None = None,
        endpoint_id: str | None = None,
        endpoint_revision_id: str | None = None,
    ) -> None:
        if issuer_token is not _MUTATION_PERMIT_ISSUER_TOKEN:
            raise TypeError("MutationPermit instances are issued only by a live lease adapter")

        self._lease_id = _require_text(lease_id, "lease_id")
        self._resource_key = _require_text(resource_key, "resource_key")
        self._owner_installation_id = _require_text(
            owner_installation_id,
            "owner_installation_id",
        )
        self._ownership_epoch = _require_positive_int(ownership_epoch, "ownership_epoch")
        self._fencing_token = _require_positive_int(fencing_token, "fencing_token")
        self._run_id = _require_text(run_id, "run_id")
        self._run_target_id = _require_text(run_target_id, "run_target_id")
        self._endpoint_id = _require_text(endpoint_id, "endpoint_id")
        self._endpoint_revision_id = _require_text(
            endpoint_revision_id,
            "endpoint_revision_id",
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("MutationPermit cannot be subclassed")

    @property
    def lease_id(self) -> str:
        return self._lease_id

    @property
    def resource_key(self) -> str:
        return self._resource_key

    @property
    def owner_installation_id(self) -> str:
        return self._owner_installation_id

    @property
    def ownership_epoch(self) -> int:
        return self._ownership_epoch

    @property
    def fencing_token(self) -> int:
        return self._fencing_token

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def run_target_id(self) -> str:
        return self._run_target_id

    @property
    def endpoint_id(self) -> str:
        return self._endpoint_id

    @property
    def endpoint_revision_id(self) -> str:
        return self._endpoint_revision_id

    def __reduce__(self) -> NoReturn:
        raise TypeError("MutationPermit is not serializable")


def _issue_mutation_permit(
    *,
    lease_id: str,
    resource_key: str,
    owner_installation_id: str,
    ownership_epoch: int,
    fencing_token: int,
    run_id: str,
    run_target_id: str,
    endpoint_id: str,
    endpoint_revision_id: str,
) -> MutationPermit:
    return MutationPermit(
        _MUTATION_PERMIT_ISSUER_TOKEN,
        lease_id=lease_id,
        resource_key=resource_key,
        owner_installation_id=owner_installation_id,
        ownership_epoch=ownership_epoch,
        fencing_token=fencing_token,
        run_id=run_id,
        run_target_id=run_target_id,
        endpoint_id=endpoint_id,
        endpoint_revision_id=endpoint_revision_id,
    )


def _require_text(value: str | None, field_name: str) -> str:
    if value is None or not value.strip():
        raise TypeError(f"MutationPermit requires {field_name}")
    return value


def _require_positive_int(value: int | None, field_name: str) -> int:
    if value is None or value < 1:
        raise TypeError(f"MutationPermit requires positive {field_name}")
    return value
