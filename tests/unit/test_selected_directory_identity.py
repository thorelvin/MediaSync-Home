from __future__ import annotations

import pytest

from mediasync_home.application.selected_directory_identity import (
    SelectedDirectoryIdentityError,
    SelectedDirectoryProbeError,
    SelectedDirectoryProbeEvidence,
    SelectedDirectoryRelationKind,
    StorageIdentityTrust,
    bind_standard_backup_draft_directory_identities,
    query_selected_directory_identities,
)
from mediasync_home.application.job_drafts import DraftTarget, StandardBackupJobDraft


class _Probe:
    def __init__(
        self,
        evidence: dict[
            str, SelectedDirectoryProbeEvidence | SelectedDirectoryProbeError
        ],
    ) -> None:
        self.evidence = evidence

    def inspect_directory(self, path_label: str) -> SelectedDirectoryProbeEvidence:
        result = self.evidence[path_label]
        if isinstance(result, SelectedDirectoryProbeError):
            raise result
        return result


def test_query_blocks_handle_alias_without_disclosing_raw_identity() -> None:
    shared = SelectedDirectoryProbeEvidence(
        object_identity_key="volume-secret:file-secret",
        final_path=r"\\?\Volume{private}\Pictures",
        storage_identity_key="disk-secret",
        storage_identity_trust=StorageIdentityTrust.CONFIRMED,
    )

    result = query_selected_directory_identities(
        path_labels=("C:/Pictures", "D:/Alias"),
        probe=_Probe({"C:/Pictures": shared, "D:/Alias": shared}),
    )

    assert result.relationships[0].kind is SelectedDirectoryRelationKind.SAME_ROOT_ALIAS
    assert result.relationships[0].blocking is True
    payload = result.to_dict()
    assert "volume-secret" not in repr(payload)
    assert "disk-secret" not in repr(payload)
    assert len(str(payload["items"][0]["independent_device_id"])) == 64  # type: ignore[index]


def test_query_blocks_nested_final_paths() -> None:
    result = query_selected_directory_identities(
        path_labels=("C:/Pictures", "D:/Backup"),
        probe=_Probe(
            {
                "C:/Pictures": _evidence(
                    object_key="source",
                    final_path=r"\\?\Volume{shared}\Users\Ada\Pictures",
                    storage_key="disk-a",
                ),
                "D:/Backup": _evidence(
                    object_key="target",
                    final_path=(r"\\?\Volume{shared}\Users\Ada\Pictures\Backup"),
                    storage_key="disk-a",
                ),
            }
        ),
    )

    assert result.relationships == (result.relationships[0],)
    assert result.relationships[0].kind is SelectedDirectoryRelationKind.ROOT_OVERLAP
    assert result.relationships[0].blocking is True


def test_query_warns_for_separate_roots_on_same_physical_device() -> None:
    result = query_selected_directory_identities(
        path_labels=("C:/Pictures", "C:/Backup", "E:/Offsite"),
        probe=_Probe(
            {
                "C:/Pictures": _evidence(
                    object_key="source",
                    final_path=r"\\?\Volume{shared}\Pictures",
                    storage_key="disk-a",
                ),
                "C:/Backup": _evidence(
                    object_key="target-a",
                    final_path=r"\\?\Volume{shared}\Backup",
                    storage_key="disk-a",
                ),
                "E:/Offsite": _evidence(
                    object_key="target-b",
                    final_path=r"\\?\Volume{offsite}\Backup",
                    storage_key="disk-b",
                ),
            }
        ),
    )

    assert [relation.kind for relation in result.relationships] == [
        SelectedDirectoryRelationKind.SAME_PHYSICAL_DEVICE
    ]
    assert result.relationships[0].blocking is False
    assert (
        result.items[0].independent_device_id == result.items[1].independent_device_id
    )
    assert (
        result.items[2].independent_device_id != result.items[1].independent_device_id
    )


def test_query_preserves_unknown_and_logical_only_topology() -> None:
    result = query_selected_directory_identities(
        path_labels=("C:/Pictures", "Z:/Backup", "Y:/Backup"),
        probe=_Probe(
            {
                "C:/Pictures": SelectedDirectoryProbeError(
                    "SELECTED_DIRECTORY_IDENTITY_EVIDENCE_UNAVAILABLE"
                ),
                "Z:/Backup": _evidence(
                    object_key="share-a",
                    final_path=r"\\server\share\one",
                    storage_key="server/share",
                    trust=StorageIdentityTrust.LOGICAL_ONLY,
                ),
                "Y:/Backup": _evidence(
                    object_key="share-b",
                    final_path=r"\\server\share\two",
                    storage_key="server/share",
                    trust=StorageIdentityTrust.LOGICAL_ONLY,
                ),
            }
        ),
    )

    assert result.items[0].status == "UNAVAILABLE"
    assert result.items[0].validation_code == (
        "SELECTED_DIRECTORY_IDENTITY_EVIDENCE_UNAVAILABLE"
    )
    assert result.items[1].independent_device_id is None
    assert result.relationships[0].kind is (
        SelectedDirectoryRelationKind.SAME_LOGICAL_STORAGE
    )


def test_draft_binding_replaces_client_device_ids_with_probe_evidence() -> None:
    draft = StandardBackupJobDraft(
        draft_id="draft-a",
        source_name="Pictures",
        source_path_label="C:/Pictures",
        targets=(
            DraftTarget(
                name="Backup",
                path_label="E:/Backup",
                independent_device_id="untrusted-client-value",
            ),
        ),
    )
    probe = _Probe(
        {
            "C:/Pictures": _evidence(
                object_key="source",
                final_path=r"\\?\Volume{source}\Pictures",
                storage_key="disk-a",
            ),
            "E:/Backup": _evidence(
                object_key="target",
                final_path=r"\\?\Volume{target}\Backup",
                storage_key="disk-b",
            ),
        }
    )

    bound = bind_standard_backup_draft_directory_identities(
        draft=draft,
        probe=probe,
    )

    assert bound.targets[0].independent_device_id != "untrusted-client-value"
    assert bound.targets[0].independent_device_id is not None
    assert len(bound.targets[0].independent_device_id or "") == 64


@pytest.mark.parametrize("paths", [(), ("a", "b", "c", "d", "e"), ("",)])
def test_query_rejects_invalid_bounded_path_sets(paths: tuple[str, ...]) -> None:
    with pytest.raises(SelectedDirectoryIdentityError):
        query_selected_directory_identities(path_labels=paths, probe=_Probe({}))


def _evidence(
    *,
    object_key: str,
    final_path: str,
    storage_key: str,
    trust: StorageIdentityTrust = StorageIdentityTrust.CONFIRMED,
) -> SelectedDirectoryProbeEvidence:
    return SelectedDirectoryProbeEvidence(
        object_identity_key=object_key,
        final_path=final_path,
        storage_identity_key=storage_key,
        storage_identity_trust=trust,
    )
