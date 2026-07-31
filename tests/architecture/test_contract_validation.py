from __future__ import annotations

import copy

import pytest

from tools import validate_contracts


def _yaml_loader():
    yaml, _, _ = validate_contracts.require_dependencies()
    return yaml


def test_repository_contracts_validate() -> None:
    summary = validate_contracts.validate_repository()

    assert summary.contracts == 9
    assert summary.json_schemas == 4
    assert summary.database_invariants >= 4
    assert summary.examples == 5
    assert summary.reason_codes >= 1
    assert summary.state_machines >= 1


def test_duplicate_reason_code_is_rejected() -> None:
    yaml = _yaml_loader()
    document = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/reason-codes.yaml",
        yaml,
    )
    duplicate = copy.deepcopy(document["codes"][0])
    document["codes"].append(duplicate)

    with pytest.raises(validate_contracts.ContractValidationError, match="duplicate reason code"):
        validate_contracts.validate_reason_codes(document)


def test_frozen_contract_requires_owner_accepted_governing_adrs() -> None:
    yaml = _yaml_loader()
    manifest = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/contracts-manifest.yaml",
        yaml,
    )
    catalog = validate_contracts.load_yaml(
        validate_contracts.ROOT / "docs/adr/catalog.yaml",
        yaml,
    )
    manifest = copy.deepcopy(manifest)
    for contract in manifest["contracts"]:
        if contract["path"] == "ipc-command.schema.json":
            contract["status"] = "frozen"
            break

    with pytest.raises(
        validate_contracts.ContractValidationError,
        match="unaccepted governing ADRs",
    ):
        validate_contracts.validate_manifest(validate_contracts.ROOT, manifest, catalog)


def test_unknown_state_transition_target_is_rejected() -> None:
    yaml = _yaml_loader()
    document = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/state-machines.yaml",
        yaml,
    )
    document = copy.deepcopy(document)
    document["machines"]["command_receipt"]["transitions"]["RECEIVED"].append("BOGUS")

    with pytest.raises(
        validate_contracts.ContractValidationError,
        match="unknown transition target",
    ):
        validate_contracts.validate_state_machines(document)


def test_database_contract_rejects_unique_file_entry_comparison_key() -> None:
    yaml = _yaml_loader()
    document = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/database-contract.yaml",
        yaml,
    )
    document = copy.deepcopy(document)
    invariant = _database_invariant(
        document,
        "DB-001_FILE_ENTRIES_COMPARISON_KEY_IS_NON_UNIQUE",
    )
    invariant["must_have_unique_keys"].append(["snapshot_id", "comparison_key"])

    with pytest.raises(
        validate_contracts.ContractValidationError,
        match="forbids unique file_entries",
    ):
        validate_contracts.validate_database_contract(document)


def test_database_contract_rejects_missing_parent_scope_foreign_key() -> None:
    yaml = _yaml_loader()
    document = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/database-contract.yaml",
        yaml,
    )
    document = copy.deepcopy(document)
    invariant = _database_invariant(document, "DB-007_PARENT_SCOPE_COMPOSITE_KEYS")
    invariant["required_composite_foreign_keys"] = [
        item
        for item in invariant["required_composite_foreign_keys"]
        if item["child_table"] != "file_entries"
    ]

    with pytest.raises(
        validate_contracts.ContractValidationError,
        match="missing required composite foreign key",
    ):
        validate_contracts.validate_database_contract(document)


def test_database_contract_rejects_missing_immutable_revision_table() -> None:
    yaml = _yaml_loader()
    document = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/database-contract.yaml",
        yaml,
    )
    document = copy.deepcopy(document)
    invariant = _database_invariant(document, "ARC-005_IMMUTABLE_REVISION_GUARDS")
    invariant["always_immutable_tables"] = [
        table
        for table in invariant["always_immutable_tables"]
        if table != "endpoint_revisions"
    ]

    with pytest.raises(
        validate_contracts.ContractValidationError,
        match="always immutable tables drifted",
    ):
        validate_contracts.validate_database_contract(document)


def test_database_contract_rejects_endpoint_generation_binding_drift() -> None:
    yaml = _yaml_loader()
    document = validate_contracts.load_yaml(
        validate_contracts.ROOT / "schema/database-contract.yaml",
        yaml,
    )
    document = copy.deepcopy(document)
    invariant = _database_invariant(document, "ARC-005_IMMUTABLE_REVISION_GUARDS")
    invariant["endpoint_generation"]["exact_bindings"][1]["columns"] = [
        "endpoint_id",
        "endpoint_revision_id",
    ]

    with pytest.raises(
        validate_contracts.ContractValidationError,
        match="endpoint generation exact bindings drifted",
    ):
        validate_contracts.validate_database_contract(document)


def _database_invariant(document: dict[str, object], invariant_id: str) -> dict[str, object]:
    invariants = document["invariants"]
    assert isinstance(invariants, list)
    for invariant in invariants:
        assert isinstance(invariant, dict)
        if invariant["id"] == invariant_id:
            return invariant
    raise AssertionError(f"missing invariant {invariant_id}")
