from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, cast

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
APPROVED_LICENSES = {
    "Apache-2.0 AND CNRI-Python",
    "Apache-2.0 OR BSD-2-Clause",
    "BSD 2-Clause License",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0 OR Apache-2.0",
    "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "MIT",
    "MPL-2.0",
    "PSF-2.0",
}
NOTICE_REQUIRED_LICENSES = {
    "Apache-2.0 AND CNRI-Python",
    "Apache-2.0 OR BSD-2-Clause",
    "CC0-1.0 OR Apache-2.0",
    "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
    "MPL-2.0",
    "PSF-2.0",
}
LICENSE_ALIASES = {
    "PSF": "PSF-2.0",
    "Python Software Foundation License": "PSF-2.0",
}
CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
}


@dataclass(frozen=True)
class PinnedRequirement:
    name: str
    version: str
    source: str


@dataclass(frozen=True)
class DependencyRecord:
    name: str
    canonical_name: str
    version: str
    license: str
    root_requirement: bool
    notice_required: bool


@dataclass(frozen=True)
class DependencyAudit:
    root_requirements: list[PinnedRequirement]
    dependencies: list[DependencyRecord]
    missing_roots: list[str]
    unapproved_licenses: list[str]
    notice_required: list[str]

    @property
    def passed(self) -> bool:
        return not self.missing_roots and not self.unapproved_licenses

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def read_pinned_requirements(path: Path) -> list[PinnedRequirement]:
    requirements: list[PinnedRequirement] = []
    _read_pinned_requirements(path.resolve(), requirements, seen=set())
    return requirements


def audit_installed_dependencies(requirements_path: Path) -> DependencyAudit:
    roots = read_pinned_requirements(requirements_path)
    root_names = {str(canonicalize_name(requirement.name)) for requirement in roots}
    closure, missing_roots = _dependency_closure(root_names)

    records: list[DependencyRecord] = []
    unapproved_licenses: list[str] = []
    notice_required: list[str] = []
    for canonical_name in sorted(closure):
        distribution = metadata.distribution(canonical_name)
        license_name = _distribution_license(distribution)
        if license_name not in APPROVED_LICENSES:
            unapproved_licenses.append(canonical_name)
        if license_name in NOTICE_REQUIRED_LICENSES:
            notice_required.append(canonical_name)
        records.append(
            DependencyRecord(
                name=distribution.metadata["Name"],
                canonical_name=canonical_name,
                version=distribution.version,
                license=license_name,
                root_requirement=canonical_name in root_names,
                notice_required=license_name in NOTICE_REQUIRED_LICENSES,
            )
        )

    return DependencyAudit(
        root_requirements=roots,
        dependencies=records,
        missing_roots=sorted(missing_roots),
        unapproved_licenses=sorted(unapproved_licenses),
        notice_required=sorted(notice_required),
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit pinned dependency and license metadata")
    parser.add_argument("--requirements", type=Path, default=ROOT / "requirements-dev.txt")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    audit = audit_installed_dependencies(args.requirements)
    payload = json.dumps(audit.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload.encode("utf-8"))
    print(
        json.dumps(
            {
                "dependencies": len(audit.dependencies),
                "missing_roots": audit.missing_roots,
                "notice_required": audit.notice_required,
                "passed": audit.passed,
                "unapproved_licenses": audit.unapproved_licenses,
            },
            sort_keys=True,
        )
    )
    return 0 if audit.passed else 2


def _read_pinned_requirements(path: Path, output: list[PinnedRequirement], *, seen: set[Path]) -> None:
    if path in seen:
        return
    seen.add(path)
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", maxsplit=1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            _read_pinned_requirements((path.parent / line[3:].strip()).resolve(), output, seen=seen)
            continue
        if "==" not in line:
            raise ValueError(f"requirement must be pinned with == at {path}:{line_number}: {line}")
        requirement = Requirement(line)
        if len(requirement.specifier) != 1 or not any(
            spec.operator == "==" for spec in requirement.specifier
        ):
            raise ValueError(f"requirement must use one exact == pin at {path}:{line_number}: {line}")
        version = next(iter(requirement.specifier)).version
        output.append(PinnedRequirement(name=requirement.name, version=version, source=path.name))


def _dependency_closure(root_names: set[str]) -> tuple[set[str], set[str]]:
    seen: set[str] = set()
    missing_roots: set[str] = set()
    queue = list(root_names)
    marker_environment = {key: str(value) for key, value in default_environment().items()}
    marker_environment["extra"] = ""
    while queue:
        canonical_name = queue.pop()
        if canonical_name in seen:
            continue
        try:
            distribution = metadata.distribution(canonical_name)
        except metadata.PackageNotFoundError:
            if canonical_name in root_names:
                missing_roots.add(canonical_name)
            continue
        seen.add(canonical_name)
        for requirement_text in distribution.requires or ():
            requirement = Requirement(requirement_text)
            if requirement.marker and not requirement.marker.evaluate(marker_environment):
                continue
            dependency_name = str(canonicalize_name(requirement.name))
            if dependency_name not in seen:
                queue.append(dependency_name)
    return seen, missing_roots


def _distribution_license(distribution: metadata.Distribution) -> str:
    metadata_payload = cast(Any, distribution.metadata)
    license_name = cast(
        str | None,
        metadata_payload.get("License-Expression") or metadata_payload.get("License"),
    )
    if license_name:
        return _normalize_license_name(license_name.strip())
    classifiers = cast(list[str], distribution.metadata.get_all("Classifier") or [])
    for classifier in classifiers:
        if classifier in CLASSIFIER_LICENSES:
            return CLASSIFIER_LICENSES[classifier]
    return "UNKNOWN"


def _normalize_license_name(license_name: str) -> str:
    return LICENSE_ALIASES.get(license_name, license_name)


if __name__ == "__main__":
    raise SystemExit(main())
