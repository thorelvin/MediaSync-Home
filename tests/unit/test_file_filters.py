from __future__ import annotations

import hashlib
import time

import pytest

from mediasync_home.application.file_filters import (
    FILTER_RULES_SCHEMA_VERSION,
    MAX_FILTER_RULES,
    MAX_GLOB_PATTERN_LENGTH,
    MAX_REGEX_INPUT_LENGTH,
    FileFilterPolicy,
    FileFilterPolicyError,
    FileFilterRule,
    FileFilterSession,
    FileFilterSubject,
    FilterAction,
    FilterRuleKind,
    canonical_file_filter_policy_json,
    default_file_filter_policy,
    parse_file_filter_policy_json,
)


def _rule(
    kind: FilterRuleKind,
    value: str | int | bool,
    *,
    action: FilterAction = FilterAction.EXCLUDE,
    rule_id: str = "rule-1",
) -> FileFilterRule:
    return FileFilterRule(
        rule_id=rule_id,
        action=action,
        kind=kind,
        value=value,
    )


def _policy(
    *rules: FileFilterRule,
    include_defaults: bool = False,
    include_only: bool = False,
    regex_enabled: bool = False,
) -> FileFilterPolicy:
    return FileFilterPolicy(
        include_default_exclusions=include_defaults,
        include_only_if_matched=include_only,
        advanced_regex_enabled=regex_enabled,
        rules=rules,
        schema_version=FILTER_RULES_SCHEMA_VERSION,
    )


def _subject(
    path: str,
    *,
    object_type: str = "file",
    size_bytes: int | None = 100,
    modified_ns: int | None = 200,
    created_ns: int | None = 150,
    file_attributes: int = 0,
    is_empty_directory: bool = False,
) -> FileFilterSubject:
    return FileFilterSubject(
        relative_path=path,
        object_type=object_type,
        size_bytes=size_bytes,
        modified_ns=modified_ns,
        created_ns=created_ns,
        file_attributes=file_attributes,
        is_empty_directory=is_empty_directory,
    )


def test_legacy_default_policy_round_trips_with_hash_evidence() -> None:
    policy = default_file_filter_policy()
    raw = canonical_file_filter_policy_json(policy)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    assert raw == '{"preset":"ALL_USER_FILES","schema_version":1}'
    assert parse_file_filter_policy_json(raw, expected_hash=digest) == policy


@pytest.mark.parametrize(
    ("raw", "expected_hash", "validation_code"),
    (
        (
            '{"preset":"ALL_USER_FILES","schema_version":1}',
            "0" * 64,
            "FILTER_RULES_HASH_MISMATCH",
        ),
        (
            '{"schema_version":1,"preset":"ALL_USER_FILES"}',
            None,
            "FILTER_RULES_NOT_CANONICAL",
        ),
        ("[]", None, "FILTER_RULES_SHAPE_INVALID"),
        ("{", None, "FILTER_RULES_JSON_INVALID"),
    ),
)
def test_policy_parser_rejects_untrusted_or_noncanonical_evidence(
    raw: str,
    expected_hash: str | None,
    validation_code: str,
) -> None:
    with pytest.raises(FileFilterPolicyError, match=validation_code):
        parse_file_filter_policy_json(raw, expected_hash=expected_hash)


def test_schema_two_policy_round_trips_canonically() -> None:
    policy = _policy(
        _rule(FilterRuleKind.EXTENSION, "jpg", action=FilterAction.INCLUDE),
        _rule(
            FilterRuleKind.RELATIVE_PATH_GLOB,
            "private/**",
            rule_id="private",
        ),
        include_defaults=True,
        include_only=True,
    )

    raw = canonical_file_filter_policy_json(policy)

    assert parse_file_filter_policy_json(raw) == policy
    assert canonical_file_filter_policy_json(parse_file_filter_policy_json(raw)) == raw


@pytest.mark.parametrize(
    "path",
    (
        "$RECYCLE.BIN",
        "$recycle.bin/deleted/photo.jpg",
        "SYSTEM VOLUME INFORMATION",
        "System Volume Information/tracking.log",
        "Thumbs.db",
        "THUMBS.DB",
        "folder/Desktop.ini",
        "cache/partial.TMP",
        "documents/~$Budget.xlsx",
    ),
)
def test_safe_defaults_exclude_system_and_temporary_paths_case_insensitively(
    path: str,
) -> None:
    decision = FileFilterSession(default_file_filter_policy()).evaluate(
        _subject(
            path,
            object_type="directory" if "/" not in path and "." not in path else "file",
        )
    )

    assert decision.included is False
    assert decision.reason_code == "FILTER_RULE_EXCLUDED"


def test_safe_defaults_keep_ordinary_user_content() -> None:
    decision = FileFilterSession(default_file_filter_policy()).evaluate(
        _subject("Family Photos/IMG_0001.JPG")
    )

    assert decision.included is True
    assert decision.reason_code == "FILTER_INCLUDED_BY_DEFAULT"


def test_explicit_include_can_override_a_safe_default() -> None:
    policy = _policy(
        _rule(
            FilterRuleKind.FILE_NAME_GLOB,
            "Thumbs.db",
            action=FilterAction.INCLUDE,
            rule_id="include-thumbs",
        ),
        include_defaults=True,
    )

    decision = FileFilterSession(policy).evaluate(_subject("archive/THUMBS.DB"))

    assert decision.included is True
    assert decision.matched_rule_id == "include-thumbs"


def test_globs_respect_path_separators_and_double_star_matches_zero_or_more_folders() -> None:
    policy = _policy(
        _rule(
            FilterRuleKind.RELATIVE_PATH_GLOB,
            "photos/**/keep?.jpg",
        )
    )
    session = FileFilterSession(policy)

    assert session.evaluate(_subject("photos/keep1.jpg")).included is False
    assert session.evaluate(_subject("photos/2026/keep2.jpg")).included is False
    assert session.evaluate(_subject("photos/2026/deep/keep3.jpg")).included is False
    assert session.evaluate(_subject("photos/2026/keep12.jpg")).included is True


def test_directory_exclusion_prunes_the_subtree() -> None:
    decision = FileFilterSession(
        _policy(_rule(FilterRuleKind.DIRECTORY_GLOB, "cache/**"))
    ).evaluate(_subject("cache", object_type="directory", size_bytes=None))

    assert decision.included is False
    assert decision.prune_directory is True


def test_include_only_mode_keeps_traversal_directories_and_only_matching_files() -> None:
    session = FileFilterSession(
        _policy(
            _rule(
                FilterRuleKind.EXTENSION,
                ".JpG",
                action=FilterAction.INCLUDE,
            ),
            include_only=True,
        )
    )

    assert session.evaluate(
        _subject("photos", object_type="directory", size_bytes=None)
    ).included
    assert session.evaluate(_subject("photos/portrait.JPG")).included
    assert not session.evaluate(_subject("photos/notes.txt")).included


@pytest.mark.parametrize(
    ("rule", "subject"),
    (
        (_rule(FilterRuleKind.MIN_SIZE_BYTES, 100), _subject("large.bin", size_bytes=100)),
        (_rule(FilterRuleKind.MAX_SIZE_BYTES, 100), _subject("small.bin", size_bytes=100)),
        (_rule(FilterRuleKind.MODIFIED_AFTER_NS, 200), _subject("new.bin", modified_ns=200)),
        (_rule(FilterRuleKind.MODIFIED_BEFORE_NS, 200), _subject("old.bin", modified_ns=200)),
        (_rule(FilterRuleKind.CREATED_AFTER_NS, 150), _subject("born.bin", created_ns=150)),
        (_rule(FilterRuleKind.CREATED_BEFORE_NS, 150), _subject("born.bin", created_ns=150)),
        (_rule(FilterRuleKind.HIDDEN_ATTRIBUTE, True), _subject("hidden.bin", file_attributes=0x2)),
        (_rule(FilterRuleKind.SYSTEM_ATTRIBUTE, True), _subject("system.bin", file_attributes=0x4)),
        (_rule(FilterRuleKind.TEMPORARY_FILE, True), _subject("temp.bin", file_attributes=0x100)),
        (_rule(FilterRuleKind.REPARSE_POINT, True), _subject("link", object_type="reparse")),
        (
            _rule(FilterRuleKind.EMPTY_DIRECTORY, True),
            _subject(
                "empty",
                object_type="directory",
                size_bytes=None,
                is_empty_directory=True,
            ),
        ),
    ),
)
def test_metadata_and_object_rules_match_at_the_documented_boundary(
    rule: FileFilterRule,
    subject: FileFilterSubject,
) -> None:
    decision = FileFilterSession(_policy(rule)).evaluate(subject)

    assert decision.included is False
    assert decision.matched_rule_id == rule.rule_id


def test_advanced_regex_is_cancellable_and_disables_after_repeated_timeouts() -> None:
    session = FileFilterSession(
        _policy(
            _rule(FilterRuleKind.REGEX, r"^(a+)+$"),
            regex_enabled=True,
        ),
        regex_match_timeout_seconds=0.000001,
        regex_total_budget_seconds=0.05,
    )
    subject = _subject("a" * 3000 + "!")
    started = time.perf_counter()

    decisions = tuple(session.evaluate(subject) for _ in range(3))

    assert time.perf_counter() - started < 0.5
    assert all(
        decision.error_code == "FILTER_REGEX_BUDGET_EXCEEDED"
        for decision in decisions
    )
    assert all(not decision.included for decision in decisions)


def test_regex_input_limit_blocks_instead_of_becoming_a_silent_nonmatch() -> None:
    session = FileFilterSession(
        _policy(
            _rule(FilterRuleKind.REGEX, ".*"),
            regex_enabled=True,
        )
    )

    decision = session.evaluate(_subject("a" * (MAX_REGEX_INPUT_LENGTH + 1)))

    assert decision.error_code == "FILTER_REGEX_BUDGET_EXCEEDED"
    assert decision.included is False


def test_rule_limits_and_advanced_mode_are_enforced_at_construction() -> None:
    with pytest.raises(FileFilterPolicyError, match="FILTER_RULE_COUNT_LIMIT_EXCEEDED"):
        _policy(
            *(
                _rule(
                    FilterRuleKind.FILE_NAME_GLOB,
                    f"file-{index}",
                    rule_id=f"rule-{index}",
                )
                for index in range(MAX_FILTER_RULES + 1)
            )
        )
    with pytest.raises(FileFilterPolicyError, match="FILTER_RULE_PATTERN_LIMIT_EXCEEDED"):
        _rule(FilterRuleKind.FILE_NAME_GLOB, "a" * (MAX_GLOB_PATTERN_LENGTH + 1))
    with pytest.raises(FileFilterPolicyError, match="FILTER_REGEX_ADVANCED_MODE_REQUIRED"):
        _policy(_rule(FilterRuleKind.REGEX, ".*"))


@pytest.mark.parametrize(
    "relative_path",
    ("", "/absolute", "folder\\file", "folder/../file", "folder//file"),
)
def test_filter_subject_rejects_noncanonical_relative_paths(relative_path: str) -> None:
    with pytest.raises(FileFilterPolicyError, match="FILTER_SUBJECT_PATH_INVALID"):
        _subject(relative_path)
