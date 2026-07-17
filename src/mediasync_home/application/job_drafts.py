from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


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


@dataclass(frozen=True)
class StandardBackupDefaults:
    behavior: BackupBehavior = BackupBehavior.UPDATE_BACKUP
    file_selection: FileSelectionPreset = FileSelectionPreset.ALL_USER_FILES
    verification: VerificationPreset = VerificationPreset.STANDARD
    retention: RetentionPreset = RetentionPreset.THIRTY_DAYS
    extra_files: ExtraFilesPreset = ExtraFilesPreset.KEEP_ON_TARGET
    performance: PerformancePreset = PerformancePreset.AUTO


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
        return tuple(issues)

    def can_create(self) -> bool:
        return not self.validation_issues()


class JobDraftStore(Protocol):
    def save_standard_backup_draft(self, draft: StandardBackupJobDraft) -> None: ...

    def load_standard_backup_draft(self, draft_id: str) -> StandardBackupJobDraft | None: ...
