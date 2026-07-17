from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_dependencies import DependencyRecord, audit_installed_dependencies  # noqa: E402

ALLOWED_SEVERITIES = {"LOW", "MODERATE", "HIGH", "CRITICAL"}


@dataclass(frozen=True)
class Advisory:
    advisory_id: str
    package: str
    affected_versions: str
    severity: str
    summary: str
    url: str
    withdrawn: bool = False

    @property
    def canonical_package(self) -> str:
        return str(canonicalize_name(self.package))


@dataclass(frozen=True)
class AdvisoryDatabase:
    schema_version: int
    generated_utc: str
    source: str
    advisories: list[Advisory]


@dataclass(frozen=True)
class ScannedDependency:
    name: str
    canonical_name: str
    version: str


@dataclass(frozen=True)
class VulnerabilityFinding:
    advisory_id: str
    package: str
    installed_version: str
    affected_versions: str
    severity: str
    summary: str
    url: str


@dataclass(frozen=True)
class VulnerabilityAudit:
    database_schema_version: int
    database_generated_utc: str
    database_source: str
    scanned_dependencies: list[ScannedDependency]
    advisories: int
    missing_roots: list[str]
    findings: list[VulnerabilityFinding]

    @property
    def passed(self) -> bool:
        return not self.missing_roots and not self.findings

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def load_advisory_database(path: Path) -> AdvisoryDatabase:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("advisory database must be a JSON object")
    schema_version = document.get("schema_version")
    if schema_version != 1:
        raise ValueError("advisory database schema_version must be 1")
    generated_utc = _required_str(document, "generated_utc")
    source = _required_str(document, "source")
    raw_advisories = document.get("advisories")
    if not isinstance(raw_advisories, list):
        raise ValueError("advisory database advisories must be a list")

    advisories = [_parse_advisory(item) for item in raw_advisories]
    advisory_ids = [advisory.advisory_id for advisory in advisories]
    if len(advisory_ids) != len(set(advisory_ids)):
        raise ValueError("advisory database contains duplicate advisory_id values")
    return AdvisoryDatabase(
        schema_version=schema_version,
        generated_utc=generated_utc,
        source=source,
        advisories=advisories,
    )


def audit_installed_vulnerabilities(
    *,
    requirements_path: Path,
    advisory_database_path: Path,
) -> VulnerabilityAudit:
    dependency_audit = audit_installed_dependencies(requirements_path)
    scanned_dependencies = [
        _dependency_record_to_scanned_dependency(dependency)
        for dependency in dependency_audit.dependencies
    ]
    advisory_database = load_advisory_database(advisory_database_path)
    findings = audit_dependencies_against_advisories(scanned_dependencies, advisory_database)
    return VulnerabilityAudit(
        database_schema_version=advisory_database.schema_version,
        database_generated_utc=advisory_database.generated_utc,
        database_source=advisory_database.source,
        scanned_dependencies=scanned_dependencies,
        advisories=len(advisory_database.advisories),
        missing_roots=dependency_audit.missing_roots,
        findings=findings,
    )


def audit_dependencies_against_advisories(
    dependencies: Iterable[ScannedDependency],
    advisory_database: AdvisoryDatabase,
) -> list[VulnerabilityFinding]:
    dependencies_by_name = {dependency.canonical_name: dependency for dependency in dependencies}
    findings: list[VulnerabilityFinding] = []
    for advisory in advisory_database.advisories:
        if advisory.withdrawn:
            continue
        dependency = dependencies_by_name.get(advisory.canonical_package)
        if dependency is None:
            continue
        if _version_matches(dependency.version, advisory.affected_versions):
            findings.append(
                VulnerabilityFinding(
                    advisory_id=advisory.advisory_id,
                    package=dependency.canonical_name,
                    installed_version=dependency.version,
                    affected_versions=advisory.affected_versions,
                    severity=advisory.severity,
                    summary=advisory.summary,
                    url=advisory.url,
                )
            )
    return sorted(findings, key=lambda finding: (finding.severity, finding.package, finding.advisory_id))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit installed dependencies against the checked-in advisory database"
    )
    parser.add_argument("--requirements", type=Path, default=ROOT / "requirements-dev.txt")
    parser.add_argument(
        "--advisories",
        type=Path,
        default=ROOT / "security/vulnerability-advisories.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    audit = audit_installed_vulnerabilities(
        requirements_path=args.requirements,
        advisory_database_path=args.advisories,
    )
    payload = json.dumps(audit.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload.encode("utf-8"))
    print(
        json.dumps(
            {
                "advisories": audit.advisories,
                "database_generated_utc": audit.database_generated_utc,
                "findings": len(audit.findings),
                "missing_roots": audit.missing_roots,
                "passed": audit.passed,
                "scanned_dependencies": len(audit.scanned_dependencies),
            },
            sort_keys=True,
        )
    )
    return 0 if audit.passed else 2


def _parse_advisory(item: object) -> Advisory:
    if not isinstance(item, dict):
        raise ValueError("advisory entries must be JSON objects")
    severity = _required_str(item, "severity").upper()
    if severity not in ALLOWED_SEVERITIES:
        raise ValueError(f"invalid advisory severity: {severity}")
    affected_versions = _required_str(item, "affected_versions")
    try:
        SpecifierSet(affected_versions)
    except InvalidSpecifier as exc:
        raise ValueError(f"invalid affected_versions specifier: {affected_versions}") from exc
    return Advisory(
        advisory_id=_required_str(item, "advisory_id"),
        package=_required_str(item, "package"),
        affected_versions=affected_versions,
        severity=severity,
        summary=_required_str(item, "summary"),
        url=_required_str(item, "url"),
        withdrawn=bool(item.get("withdrawn", False)),
    )


def _version_matches(version: str, affected_versions: str) -> bool:
    try:
        parsed_version = Version(version)
    except InvalidVersion as exc:
        raise ValueError(f"installed dependency has invalid version: {version}") from exc
    return parsed_version in SpecifierSet(affected_versions)


def _required_str(document: dict[str, Any], key: str) -> str:
    value = cast(object, document.get(key))
    if not isinstance(value, str) or not value:
        raise ValueError(f"advisory database field {key} must be a non-empty string")
    return value


def _dependency_record_to_scanned_dependency(dependency: DependencyRecord) -> ScannedDependency:
    return ScannedDependency(
        name=dependency.name,
        canonical_name=dependency.canonical_name,
        version=dependency.version,
    )


if __name__ == "__main__":
    raise SystemExit(main())
