from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import regex  # type: ignore[import-untyped]


LEGACY_FILTER_RULES_SCHEMA_VERSION = 1
FILTER_RULES_SCHEMA_VERSION = 2
MAX_FILTER_RULES = 128
MAX_GLOB_PATTERN_LENGTH = 512
MAX_REGEX_RULES = 8
MAX_REGEX_PATTERN_LENGTH = 256
MAX_REGEX_INPUT_LENGTH = 4096
DEFAULT_REGEX_MATCH_TIMEOUT_SECONDS = 0.005
DEFAULT_REGEX_TOTAL_BUDGET_SECONDS = 2.0
MAX_REGEX_TIMEOUTS_PER_RULE = 2
_RULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class FileFilterPolicyError(ValueError):
    def __init__(self, validation_code: str) -> None:
        super().__init__(validation_code)
        self.validation_code = validation_code


class FilterAction(str, Enum):
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"


class FilterRuleKind(str, Enum):
    EXTENSION = "EXTENSION"
    FILE_NAME_GLOB = "FILE_NAME_GLOB"
    RELATIVE_PATH_GLOB = "RELATIVE_PATH_GLOB"
    DIRECTORY_GLOB = "DIRECTORY_GLOB"
    MIN_SIZE_BYTES = "MIN_SIZE_BYTES"
    MAX_SIZE_BYTES = "MAX_SIZE_BYTES"
    MODIFIED_AFTER_NS = "MODIFIED_AFTER_NS"
    MODIFIED_BEFORE_NS = "MODIFIED_BEFORE_NS"
    CREATED_AFTER_NS = "CREATED_AFTER_NS"
    CREATED_BEFORE_NS = "CREATED_BEFORE_NS"
    HIDDEN_ATTRIBUTE = "HIDDEN_ATTRIBUTE"
    SYSTEM_ATTRIBUTE = "SYSTEM_ATTRIBUTE"
    TEMPORARY_FILE = "TEMPORARY_FILE"
    REPARSE_POINT = "REPARSE_POINT"
    EMPTY_DIRECTORY = "EMPTY_DIRECTORY"
    REGEX = "REGEX"


_PRE_STAT_RULE_KINDS = frozenset(
    {
        FilterRuleKind.EXTENSION,
        FilterRuleKind.FILE_NAME_GLOB,
        FilterRuleKind.RELATIVE_PATH_GLOB,
        FilterRuleKind.DIRECTORY_GLOB,
    }
)


@dataclass(frozen=True, slots=True)
class FileFilterRule:
    rule_id: str
    action: FilterAction
    kind: FilterRuleKind
    value: str | int | bool

    def __post_init__(self) -> None:
        if not isinstance(self.action, FilterAction) or not isinstance(
            self.kind, FilterRuleKind
        ):
            raise FileFilterPolicyError("FILTER_RULE_ENUM_INVALID")
        if _RULE_ID_PATTERN.fullmatch(self.rule_id) is None:
            raise FileFilterPolicyError("FILTER_RULE_ID_INVALID")
        if self.kind in {
            FilterRuleKind.EXTENSION,
            FilterRuleKind.FILE_NAME_GLOB,
            FilterRuleKind.RELATIVE_PATH_GLOB,
            FilterRuleKind.DIRECTORY_GLOB,
            FilterRuleKind.REGEX,
        }:
            if not isinstance(self.value, str) or not self.value:
                raise FileFilterPolicyError("FILTER_RULE_TEXT_VALUE_INVALID")
            limit = (
                MAX_REGEX_PATTERN_LENGTH
                if self.kind is FilterRuleKind.REGEX
                else MAX_GLOB_PATTERN_LENGTH
            )
            if len(self.value) > limit or "\0" in self.value:
                raise FileFilterPolicyError("FILTER_RULE_PATTERN_LIMIT_EXCEEDED")
            if self.kind is FilterRuleKind.EXTENSION and (
                "/" in self.value or "\\" in self.value
            ):
                raise FileFilterPolicyError("FILTER_RULE_EXTENSION_INVALID")
            return
        if self.kind in {
            FilterRuleKind.MIN_SIZE_BYTES,
            FilterRuleKind.MAX_SIZE_BYTES,
            FilterRuleKind.MODIFIED_AFTER_NS,
            FilterRuleKind.MODIFIED_BEFORE_NS,
            FilterRuleKind.CREATED_AFTER_NS,
            FilterRuleKind.CREATED_BEFORE_NS,
        }:
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, int)
                or self.value < 0
            ):
                raise FileFilterPolicyError("FILTER_RULE_INTEGER_VALUE_INVALID")
            return
        if self.value is not True:
            raise FileFilterPolicyError("FILTER_RULE_BOOLEAN_VALUE_INVALID")


@dataclass(frozen=True, slots=True)
class FileFilterPolicy:
    preset: str = "ALL_USER_FILES"
    include_default_exclusions: bool = True
    include_only_if_matched: bool = False
    advanced_regex_enabled: bool = False
    rules: tuple[FileFilterRule, ...] = ()
    schema_version: int = FILTER_RULES_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, bool)
            for value in (
                self.include_default_exclusions,
                self.include_only_if_matched,
                self.advanced_regex_enabled,
            )
        ):
            raise FileFilterPolicyError("FILTER_RULES_BOOLEAN_INVALID")
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise FileFilterPolicyError("FILTER_RULES_SCHEMA_UNSUPPORTED")
        if self.preset != "ALL_USER_FILES":
            raise FileFilterPolicyError("FILTER_PRESET_UNSUPPORTED")
        if self.schema_version not in {
            LEGACY_FILTER_RULES_SCHEMA_VERSION,
            FILTER_RULES_SCHEMA_VERSION,
        }:
            raise FileFilterPolicyError("FILTER_RULES_SCHEMA_UNSUPPORTED")
        if len(self.rules) > MAX_FILTER_RULES:
            raise FileFilterPolicyError("FILTER_RULE_COUNT_LIMIT_EXCEEDED")
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise FileFilterPolicyError("FILTER_RULE_IDS_NOT_UNIQUE")
        regex_count = sum(
            rule.kind is FilterRuleKind.REGEX for rule in self.rules
        )
        if regex_count > MAX_REGEX_RULES:
            raise FileFilterPolicyError("FILTER_REGEX_RULE_COUNT_LIMIT_EXCEEDED")
        if regex_count and not self.advanced_regex_enabled:
            raise FileFilterPolicyError("FILTER_REGEX_ADVANCED_MODE_REQUIRED")
        if self.schema_version == LEGACY_FILTER_RULES_SCHEMA_VERSION and (
            not self.include_default_exclusions
            or self.include_only_if_matched
            or self.advanced_regex_enabled
            or self.rules
        ):
            raise FileFilterPolicyError("FILTER_RULES_LEGACY_SHAPE_INVALID")


@dataclass(frozen=True, slots=True)
class FileFilterSubject:
    relative_path: str
    object_type: str
    size_bytes: int | None = None
    modified_ns: int | None = None
    created_ns: int | None = None
    file_attributes: int = 0
    is_empty_directory: bool = False

    def __post_init__(self) -> None:
        if (
            not self.relative_path
            or "\0" in self.relative_path
            or "\\" in self.relative_path
            or self.relative_path.startswith("/")
            or any(part in {"", ".", ".."} for part in self.relative_path.split("/"))
        ):
            raise FileFilterPolicyError("FILTER_SUBJECT_PATH_INVALID")
        if self.object_type not in {"file", "directory", "reparse", "other"}:
            raise FileFilterPolicyError("FILTER_SUBJECT_TYPE_INVALID")
        for value in (self.size_bytes, self.modified_ns, self.created_ns):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise FileFilterPolicyError("FILTER_SUBJECT_METADATA_INVALID")
        if (
            isinstance(self.file_attributes, bool)
            or not isinstance(self.file_attributes, int)
            or self.file_attributes < 0
        ):
            raise FileFilterPolicyError("FILTER_SUBJECT_ATTRIBUTES_INVALID")
        if not isinstance(self.is_empty_directory, bool):
            raise FileFilterPolicyError("FILTER_SUBJECT_EMPTY_DIRECTORY_INVALID")
        if self.is_empty_directory and self.object_type != "directory":
            raise FileFilterPolicyError("FILTER_SUBJECT_EMPTY_DIRECTORY_INVALID")

    @property
    def name(self) -> str:
        return self.relative_path.rsplit("/", maxsplit=1)[-1]


@dataclass(frozen=True, slots=True)
class FileFilterDecision:
    included: bool
    reason_code: str
    matched_rule_id: str | None = None
    error_code: str | None = None
    prune_directory: bool = False


_DEFAULT_RULES = (
    FileFilterRule(
        "default-recycle-bin",
        FilterAction.EXCLUDE,
        FilterRuleKind.RELATIVE_PATH_GLOB,
        "$RECYCLE.BIN/**",
    ),
    FileFilterRule(
        "default-system-volume-information",
        FilterAction.EXCLUDE,
        FilterRuleKind.RELATIVE_PATH_GLOB,
        "System Volume Information/**",
    ),
    FileFilterRule(
        "default-thumbs-db",
        FilterAction.EXCLUDE,
        FilterRuleKind.FILE_NAME_GLOB,
        "Thumbs.db",
    ),
    FileFilterRule(
        "default-desktop-ini",
        FilterAction.EXCLUDE,
        FilterRuleKind.FILE_NAME_GLOB,
        "Desktop.ini",
    ),
    FileFilterRule(
        "default-tmp-extension",
        FilterAction.EXCLUDE,
        FilterRuleKind.FILE_NAME_GLOB,
        "*.tmp",
    ),
    FileFilterRule(
        "default-office-temp",
        FilterAction.EXCLUDE,
        FilterRuleKind.FILE_NAME_GLOB,
        "~$*",
    ),
)


class FileFilterSession:
    def __init__(
        self,
        policy: FileFilterPolicy,
        *,
        regex_match_timeout_seconds: float = DEFAULT_REGEX_MATCH_TIMEOUT_SECONDS,
        regex_total_budget_seconds: float = DEFAULT_REGEX_TOTAL_BUDGET_SECONDS,
    ) -> None:
        if regex_match_timeout_seconds <= 0 or regex_total_budget_seconds <= 0:
            raise FileFilterPolicyError("FILTER_REGEX_BUDGET_INVALID")
        self._policy = policy
        self._regex_match_timeout_seconds = regex_match_timeout_seconds
        self._regex_total_budget_seconds = regex_total_budget_seconds
        self._regex_elapsed_seconds = 0.0
        self._regex_timeout_counts: dict[str, int] = {}
        self._disabled_regex_rule_ids: set[str] = set()
        self._glob_patterns: dict[str, re.Pattern[str]] = {}
        self._regex_patterns: dict[str, regex.Pattern[str]] = {}
        for rule in (*(_DEFAULT_RULES if policy.include_default_exclusions else ()), *policy.rules):
            if rule.kind in {
                FilterRuleKind.FILE_NAME_GLOB,
                FilterRuleKind.RELATIVE_PATH_GLOB,
                FilterRuleKind.DIRECTORY_GLOB,
            }:
                self._glob_patterns[rule.rule_id] = _compile_glob(str(rule.value))
            elif rule.kind is FilterRuleKind.REGEX:
                try:
                    self._regex_patterns[rule.rule_id] = regex.compile(
                        str(rule.value),
                        regex.VERSION1 | regex.IGNORECASE | regex.FULLCASE,
                    )
                except regex.error as exc:
                    raise FileFilterPolicyError("FILTER_REGEX_PATTERN_INVALID") from exc

    @property
    def policy(self) -> FileFilterPolicy:
        return self._policy

    @property
    def can_evaluate_before_metadata(self) -> bool:
        return all(
            rule.kind in _PRE_STAT_RULE_KINDS
            for rule in self._effective_rules()
        )

    @property
    def has_empty_directory_rules(self) -> bool:
        return any(
            rule.kind is FilterRuleKind.EMPTY_DIRECTORY
            for rule in self._policy.rules
        )

    def evaluate(self, subject: FileFilterSubject) -> FileFilterDecision:
        included = not (
            self._policy.include_only_if_matched and subject.object_type != "directory"
        )
        matched_rule: FileFilterRule | None = None
        for rule in self._effective_rules():
            matched, error_code = self._matches(rule, subject)
            if error_code is not None:
                return FileFilterDecision(
                    included=False,
                    reason_code=error_code,
                    matched_rule_id=rule.rule_id,
                    error_code=error_code,
                )
            if not matched:
                continue
            matched_rule = rule
            included = rule.action is FilterAction.INCLUDE
        if matched_rule is None:
            return FileFilterDecision(
                included=included,
                reason_code=(
                    "FILTER_INCLUDED_BY_DEFAULT"
                    if included
                    else "FILTER_INCLUDE_RULE_NOT_MATCHED"
                ),
            )
        return FileFilterDecision(
            included=included,
            reason_code=(
                "FILTER_RULE_INCLUDED" if included else "FILTER_RULE_EXCLUDED"
            ),
            matched_rule_id=matched_rule.rule_id,
            prune_directory=(
                not included and subject.object_type == "directory"
            ),
        )

    def _effective_rules(self) -> tuple[FileFilterRule, ...]:
        return (
            *(_DEFAULT_RULES if self._policy.include_default_exclusions else ()),
            *self._policy.rules,
        )

    def _matches(
        self,
        rule: FileFilterRule,
        subject: FileFilterSubject,
    ) -> tuple[bool, str | None]:
        value = rule.value
        if rule.kind is FilterRuleKind.EXTENSION:
            extension = subject.name.rpartition(".")[2] if "." in subject.name else ""
            return (
                subject.object_type == "file"
                and extension.casefold() == str(value).lstrip(".").casefold(),
                None,
            )
        if rule.kind is FilterRuleKind.FILE_NAME_GLOB:
            return bool(
                self._glob_patterns[rule.rule_id].fullmatch(subject.name.casefold())
            ), None
        if rule.kind is FilterRuleKind.RELATIVE_PATH_GLOB:
            return bool(
                self._glob_patterns[rule.rule_id].fullmatch(
                    subject.relative_path.casefold()
                )
            ), None
        if rule.kind is FilterRuleKind.DIRECTORY_GLOB:
            return (
                subject.object_type == "directory"
                and bool(
                    self._glob_patterns[rule.rule_id].fullmatch(
                        subject.relative_path.casefold()
                    )
                ),
                None,
            )
        if rule.kind is FilterRuleKind.MIN_SIZE_BYTES:
            return (
                subject.object_type == "file"
                and subject.size_bytes is not None
                and subject.size_bytes >= int(value),
                None,
            )
        if rule.kind is FilterRuleKind.MAX_SIZE_BYTES:
            return (
                subject.object_type == "file"
                and subject.size_bytes is not None
                and subject.size_bytes <= int(value),
                None,
            )
        if rule.kind is FilterRuleKind.MODIFIED_AFTER_NS:
            return (
                subject.object_type == "file"
                and subject.modified_ns is not None
                and subject.modified_ns >= int(value),
                None,
            )
        if rule.kind is FilterRuleKind.MODIFIED_BEFORE_NS:
            return (
                subject.object_type == "file"
                and subject.modified_ns is not None
                and subject.modified_ns <= int(value),
                None,
            )
        if rule.kind is FilterRuleKind.CREATED_AFTER_NS:
            return (
                subject.object_type == "file"
                and subject.created_ns is not None
                and subject.created_ns >= int(value),
                None,
            )
        if rule.kind is FilterRuleKind.CREATED_BEFORE_NS:
            return (
                subject.object_type == "file"
                and subject.created_ns is not None
                and subject.created_ns <= int(value),
                None,
            )
        if rule.kind is FilterRuleKind.HIDDEN_ATTRIBUTE:
            return bool(subject.file_attributes & 0x2), None
        if rule.kind is FilterRuleKind.SYSTEM_ATTRIBUTE:
            return bool(subject.file_attributes & 0x4), None
        if rule.kind is FilterRuleKind.TEMPORARY_FILE:
            return bool(subject.file_attributes & 0x100), None
        if rule.kind is FilterRuleKind.REPARSE_POINT:
            return subject.object_type == "reparse", None
        if rule.kind is FilterRuleKind.EMPTY_DIRECTORY:
            return (
                subject.object_type == "directory" and subject.is_empty_directory,
                None,
            )
        if rule.kind is FilterRuleKind.REGEX:
            return self._regex_matches(rule, subject.relative_path)
        raise AssertionError(f"unhandled filter rule kind: {rule.kind}")

    def _regex_matches(
        self,
        rule: FileFilterRule,
        value: str,
    ) -> tuple[bool, str | None]:
        if len(value) > MAX_REGEX_INPUT_LENGTH:
            return False, "FILTER_REGEX_BUDGET_EXCEEDED"
        if rule.rule_id in self._disabled_regex_rule_ids:
            return False, "FILTER_REGEX_BUDGET_EXCEEDED"
        remaining = self._regex_total_budget_seconds - self._regex_elapsed_seconds
        if remaining <= 0:
            self._disabled_regex_rule_ids.add(rule.rule_id)
            return False, "FILTER_REGEX_BUDGET_EXCEEDED"
        started = time.perf_counter()
        try:
            matched = self._regex_patterns[rule.rule_id].search(
                value,
                timeout=min(self._regex_match_timeout_seconds, remaining),
            )
        except TimeoutError:
            timeout_count = self._regex_timeout_counts.get(rule.rule_id, 0) + 1
            self._regex_timeout_counts[rule.rule_id] = timeout_count
            if timeout_count >= MAX_REGEX_TIMEOUTS_PER_RULE:
                self._disabled_regex_rule_ids.add(rule.rule_id)
            return False, "FILTER_REGEX_BUDGET_EXCEEDED"
        finally:
            self._regex_elapsed_seconds += time.perf_counter() - started
        return matched is not None, None


def default_file_filter_policy() -> FileFilterPolicy:
    return FileFilterPolicy(schema_version=LEGACY_FILTER_RULES_SCHEMA_VERSION)


def parse_file_filter_policy_json(
    raw: str,
    *,
    expected_hash: str | None = None,
) -> FileFilterPolicy:
    if (
        expected_hash is not None
        and hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected_hash
    ):
        raise FileFilterPolicyError("FILTER_RULES_HASH_MISMATCH")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FileFilterPolicyError("FILTER_RULES_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise FileFilterPolicyError("FILTER_RULES_SHAPE_INVALID")
    schema_version = payload.get("schema_version")
    if schema_version == LEGACY_FILTER_RULES_SCHEMA_VERSION:
        if set(payload) != {"preset", "schema_version"}:
            raise FileFilterPolicyError("FILTER_RULES_LEGACY_SHAPE_INVALID")
        policy = FileFilterPolicy(
            preset=_required_text(payload, "preset"),
            schema_version=LEGACY_FILTER_RULES_SCHEMA_VERSION,
        )
    elif schema_version == FILTER_RULES_SCHEMA_VERSION:
        if set(payload) != {
            "advanced_regex_enabled",
            "include_default_exclusions",
            "include_only_if_matched",
            "preset",
            "rules",
            "schema_version",
        }:
            raise FileFilterPolicyError("FILTER_RULES_SHAPE_INVALID")
        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list):
            raise FileFilterPolicyError("FILTER_RULES_LIST_INVALID")
        policy = FileFilterPolicy(
            preset=_required_text(payload, "preset"),
            include_default_exclusions=_required_bool(
                payload, "include_default_exclusions"
            ),
            include_only_if_matched=_required_bool(
                payload, "include_only_if_matched"
            ),
            advanced_regex_enabled=_required_bool(
                payload, "advanced_regex_enabled"
            ),
            rules=tuple(_parse_rule(item) for item in raw_rules),
            schema_version=FILTER_RULES_SCHEMA_VERSION,
        )
    else:
        raise FileFilterPolicyError("FILTER_RULES_SCHEMA_UNSUPPORTED")
    if raw != canonical_file_filter_policy_json(policy):
        raise FileFilterPolicyError("FILTER_RULES_NOT_CANONICAL")
    return policy


def canonical_file_filter_policy_json(policy: FileFilterPolicy) -> str:
    if policy.schema_version == LEGACY_FILTER_RULES_SCHEMA_VERSION:
        payload: dict[str, object] = {
            "preset": policy.preset,
            "schema_version": LEGACY_FILTER_RULES_SCHEMA_VERSION,
        }
    else:
        payload = {
            "advanced_regex_enabled": policy.advanced_regex_enabled,
            "include_default_exclusions": policy.include_default_exclusions,
            "include_only_if_matched": policy.include_only_if_matched,
            "preset": policy.preset,
            "rules": [
                {
                    "action": rule.action.value,
                    "kind": rule.kind.value,
                    "rule_id": rule.rule_id,
                    "value": (
                        str(rule.value).lstrip(".").casefold()
                        if rule.kind is FilterRuleKind.EXTENSION
                        else rule.value
                    ),
                }
                for rule in policy.rules
            ],
            "schema_version": FILTER_RULES_SCHEMA_VERSION,
        }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _parse_rule(payload: object) -> FileFilterRule:
    if not isinstance(payload, dict) or set(payload) != {
        "action",
        "kind",
        "rule_id",
        "value",
    }:
        raise FileFilterPolicyError("FILTER_RULE_SHAPE_INVALID")
    try:
        kind = FilterRuleKind(_required_text(payload, "kind"))
        action = FilterAction(_required_text(payload, "action"))
    except ValueError as exc:
        raise FileFilterPolicyError("FILTER_RULE_ENUM_INVALID") from exc
    value = payload.get("value")
    if not isinstance(value, (str, int, bool)):
        raise FileFilterPolicyError("FILTER_RULE_VALUE_INVALID")
    return FileFilterRule(
        rule_id=_required_text(payload, "rule_id"),
        action=action,
        kind=kind,
        value=value,
    )


def _required_text(payload: Mapping[object, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise FileFilterPolicyError("FILTER_RULES_TEXT_INVALID")
    return value


def _required_bool(payload: Mapping[object, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise FileFilterPolicyError("FILTER_RULES_BOOLEAN_INVALID")
    return value


def _compile_glob(pattern: str) -> re.Pattern[str]:
    normalized = pattern.replace("\\", "/").casefold()
    if normalized.endswith("/**"):
        base = normalized[:-3].rstrip("/")
        expression = _glob_expression(base)
        return re.compile(rf"{expression}(?:/.*)?", re.DOTALL)
    return re.compile(_glob_expression(normalized), re.DOTALL)


def _glob_expression(pattern: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                if index + 2 < len(pattern) and pattern[index + 2] == "/":
                    output.append("(?:.*/)?")
                    index += 3
                else:
                    output.append(".*")
                    index += 2
            else:
                output.append("[^/]*")
                index += 1
            continue
        if character == "?":
            output.append("[^/]")
        else:
            output.append(re.escape(character))
        index += 1
    return "".join(output)
