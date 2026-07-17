from __future__ import annotations

import copy

import pytest

from tools import validate_contracts


def _yaml_loader():
    yaml, _, _ = validate_contracts.require_dependencies()
    return yaml


def test_repository_contracts_validate() -> None:
    summary = validate_contracts.validate_repository()

    assert summary.contracts == 8
    assert summary.json_schemas == 4
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
