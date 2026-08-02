from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from urllib.parse import urlparse


MAX_STANDARD_BACKUP_TARGETS = 3
STANDARD_BACKUP_DRAFT_SCHEMA_VERSION = 1


class DraftValidationCode(str, Enum):
    SOURCE_REQUIRED = "SOURCE_REQUIRED"
    SOURCE_LABEL_REQUIRED = "SOURCE_LABEL_REQUIRED"
    TARGET_REQUIRED = "TARGET_REQUIRED"
    TOO_MANY_TARGETS = "TOO_MANY_TARGETS"
    TARGET_NAME_REQUIRED = "TARGET_NAME_REQUIRED"
    TARGET_LABEL_REQUIRED = "TARGET_LABEL_REQUIRED"
    DUPLICATE_TARGET_NAME = "DUPLICATE_TARGET_NAME"
    TARGET_ROOT_OVERLAPS_SOURCE = "TARGET_ROOT_OVERLAPS_SOURCE"
    TARGET_ROOT_OVERLAPS_TARGET = "TARGET_ROOT_OVERLAPS_TARGET"


class DraftValidationError(ValueError):
    pass


class BackupBehavior(str, Enum):
    UPDATE_BACKUP = "UPDATE_BACKUP"


class FileSelectionPreset(str, Enum):
    ALL_USER_FILES = "ALL_USER_FILES"


class VerificationPreset(str, Enum):
    STANDARD = "STANDARD"


class RetentionPreset(str, Enum):
    THIRTY_DAYS = "THIRTY_DAYS"


class ExtraFilesPreset(str, Enum):
    KEEP_ON_TARGET = "KEEP_ON_TARGET"


class PerformancePreset(str, Enum):
    AUTO = "AUTO"


class AutomationPolicy(str, Enum):
    NEW_FILES_ONLY = "NEW_FILES_ONLY"
    NEW_AND_CHANGED_WITH_VERSIONS = "NEW_AND_CHANGED_WITH_VERSIONS"
    ANALYZE_ONLY = "ANALYZE_ONLY"


@dataclass(frozen=True)
class StandardBackupDefaults:
    behavior: BackupBehavior = BackupBehavior.UPDATE_BACKUP
    file_selection: FileSelectionPreset = FileSelectionPreset.ALL_USER_FILES
    verification: VerificationPreset = VerificationPreset.STANDARD
    retention: RetentionPreset = RetentionPreset.THIRTY_DAYS
    extra_files: ExtraFilesPreset = ExtraFilesPreset.KEEP_ON_TARGET
    performance: PerformancePreset = PerformancePreset.AUTO
    automation_policy: AutomationPolicy = AutomationPolicy.NEW_FILES_ONLY


@dataclass(frozen=True)
class DraftTarget:
    name: str
    path_label: str
    independent_device_id: str | None = None


@dataclass(frozen=True)
class DraftValidationIssue:
    code: DraftValidationCode
    field: str


@dataclass(frozen=True)
class StandardBackupJobDraft:
    draft_id: str
    source_name: str | None = None
    source_path_label: str | None = None
    targets: tuple[DraftTarget, ...] = ()
    defaults: StandardBackupDefaults = field(default_factory=StandardBackupDefaults)
    schema_version: int = STANDARD_BACKUP_DRAFT_SCHEMA_VERSION

    @classmethod
    def new(cls, draft_id: str) -> "StandardBackupJobDraft":
        return cls(draft_id=draft_id)

    def with_source(self, *, name: str, path_label: str) -> "StandardBackupJobDraft":
        if not name.strip():
            raise DraftValidationError(DraftValidationCode.SOURCE_REQUIRED.value)
        if not path_label.strip():
            raise DraftValidationError(DraftValidationCode.SOURCE_LABEL_REQUIRED.value)
        return StandardBackupJobDraft(
            draft_id=self.draft_id,
            source_name=name,
            source_path_label=path_label,
            targets=self.targets,
            defaults=self.defaults,
            schema_version=self.schema_version,
        )

    def with_added_target(
        self,
        *,
        name: str,
        path_label: str,
        independent_device_id: str | None = None,
    ) -> "StandardBackupJobDraft":
        if len(self.targets) >= MAX_STANDARD_BACKUP_TARGETS:
            raise DraftValidationError(DraftValidationCode.TOO_MANY_TARGETS.value)
        target = DraftTarget(
            name=name,
            path_label=path_label,
            independent_device_id=independent_device_id,
        )
        return StandardBackupJobDraft(
            draft_id=self.draft_id,
            source_name=self.source_name,
            source_path_label=self.source_path_label,
            targets=(*self.targets, target),
            defaults=self.defaults,
            schema_version=self.schema_version,
        )

    def validation_issues(self) -> tuple[DraftValidationIssue, ...]:
        issues: list[DraftValidationIssue] = []
        if not self.source_name or not self.source_name.strip():
            issues.append(DraftValidationIssue(DraftValidationCode.SOURCE_REQUIRED, "source_name"))
        if not self.source_path_label or not self.source_path_label.strip():
            issues.append(DraftValidationIssue(DraftValidationCode.SOURCE_LABEL_REQUIRED, "source_path_label"))
        if not self.targets:
            issues.append(DraftValidationIssue(DraftValidationCode.TARGET_REQUIRED, "targets"))
        if len(self.targets) > MAX_STANDARD_BACKUP_TARGETS:
            issues.append(DraftValidationIssue(DraftValidationCode.TOO_MANY_TARGETS, "targets"))

        seen_target_names: set[str] = set()
        seen_target_roots: list[_DraftRootKey] = []
        source_root = _draft_root_key(self.source_path_label)
        for index, target in enumerate(self.targets):
            field_prefix = f"targets[{index}]"
            normalized_name = target.name.strip().casefold()
            if not normalized_name:
                issues.append(DraftValidationIssue(DraftValidationCode.TARGET_NAME_REQUIRED, f"{field_prefix}.name"))
            elif normalized_name in seen_target_names:
                issues.append(DraftValidationIssue(DraftValidationCode.DUPLICATE_TARGET_NAME, f"{field_prefix}.name"))
            else:
                seen_target_names.add(normalized_name)
            if not target.path_label.strip():
                issues.append(
                    DraftValidationIssue(DraftValidationCode.TARGET_LABEL_REQUIRED, f"{field_prefix}.path_label")
                )
                continue
            target_root = _draft_root_key(target.path_label)
            if target_root is None:
                continue
            if source_root is not None and _roots_overlap(source_root, target_root):
                issues.append(
                    DraftValidationIssue(
                        DraftValidationCode.TARGET_ROOT_OVERLAPS_SOURCE,
                        f"{field_prefix}.path_label",
                    )
                )
            if any(_roots_overlap(existing_root, target_root) for existing_root in seen_target_roots):
                issues.append(
                    DraftValidationIssue(
                        DraftValidationCode.TARGET_ROOT_OVERLAPS_TARGET,
                        f"{field_prefix}.path_label",
                    )
                )
            seen_target_roots.append(target_root)
        return tuple(issues)

    def can_create(self) -> bool:
        return not self.validation_issues()


class JobDraftStore(Protocol):
    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None: ...

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None: ...


def draft_path_labels_overlap(first_path_label: str | None, second_path_label: str | None) -> bool:
    first = _draft_root_key(first_path_label)
    second = _draft_root_key(second_path_label)
    return first is not None and second is not None and _roots_overlap(first, second)


@dataclass(frozen=True)
class _DraftRootKey:
    anchor: str
    parts: tuple[str, ...]


def _draft_root_key(path_label: str | None) -> _DraftRootKey | None:
    if path_label is None:
        return None
    value = path_label.strip()
    if not value:
        return None
    drive_candidate = value.replace("\\", "/")
    if len(drive_candidate) >= 2 and drive_candidate[1] == ":" and drive_candidate[0].isalpha():
        return _drive_root_key(drive_candidate)
    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme.lower() != "file":
            return None
        value = _file_uri_to_windows_path(parsed.netloc, parsed.path)
    normalized = value.replace("\\", "/").strip()
    if normalized.startswith("//"):
        return _unc_root_key(normalized)
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        return _drive_root_key(normalized)
    return None


def _file_uri_to_windows_path(netloc: str, path: str) -> str:
    normalized_path = path.replace("\\", "/")
    if netloc:
        return f"//{netloc}{normalized_path}"
    if len(normalized_path) >= 3 and normalized_path[0] == "/" and normalized_path[2] == ":":
        return normalized_path[1:]
    return normalized_path


def _unc_root_key(value: str) -> _DraftRootKey | None:
    parts = _path_parts(value[2:])
    if len(parts) < 2:
        return None
    return _DraftRootKey(
        anchor=f"unc://{parts[0]}/{parts[1]}",
        parts=parts[2:],
    )


def _drive_root_key(value: str) -> _DraftRootKey:
    return _DraftRootKey(
        anchor=f"drive:{value[0].casefold()}",
        parts=_path_parts(value[2:]),
    )


def _path_parts(value: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in value.strip("/").split("/") if part and part != ".")


def _roots_overlap(first: _DraftRootKey, second: _DraftRootKey) -> bool:
    if first.anchor != second.anchor:
        return False
    shorter, longer = (
        (first.parts, second.parts)
        if len(first.parts) <= len(second.parts)
        else (second.parts, first.parts)
    )
    return longer[: len(shorter)] == shorter
