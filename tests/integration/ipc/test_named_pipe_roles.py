from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from mediasync_home.application.command_payloads import canonical_command_payload_hash
from mediasync_home.application.host_locator import (
    LOCAL_ENGINE_HOST_PUBLICATION_FILENAME,
    build_local_engine_host_publication,
)


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 named-pipe role wiring is Windows-only")


if os.name == "nt":
    from mediasync_home.adapters.host_mutex import LocalEngineHostMutex
    from mediasync_home.adapters.local_host_locator import (
        build_local_engine_host_descriptor_for_user,
        publish_local_engine_host_publication,
    )
    from mediasync_home.ipc import win32_named_pipe


def test_engine_host_and_gui_roles_complete_non_mutating_status_roundtrip() -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-integration-test",
        suffix=uuid4().hex,
    )
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-status",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert str(UUID(gui_response["request_id"])) == gui_response["request_id"]
    assert gui_response["payload"]["host_status"]["role"] == "engine-host"
    assert gui_response["payload"]["host_status"]["mutations_enabled"] is False
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[-1]["served_requests"] == 2


def test_gui_creates_durable_backup_job_through_local_writable_pipe(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-job-create-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    draft_id = str(uuid4())
    payload: dict[str, object] = {
        "draft_id": draft_id,
        "draft": {
            "draft_id": draft_id,
            "schema_version": 1,
            "source_name": "Source",
            "source_path_label": str(source_root),
            "targets": [
                {
                    "name": "Target",
                    "path_label": str(target_root),
                    "independent_device_id": None,
                }
            ],
        },
    }
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
            "--enable-local-mutations",
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--submit-command",
                "CREATE_STANDARD_BACKUP_JOB",
                "--request-id",
                str(uuid4()),
                "--idempotency-key",
                str(uuid4()),
                "--payload-json",
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "--payload-hash",
                canonical_command_payload_hash(payload),
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    with sqlite3.connect(state_root / "catalog.sqlite") as connection:
        persisted = connection.execute(
            """
            SELECT details.source_path_label, details.targets_json, receipts.state
            FROM standard_backup_job_revision_details AS details
            INNER JOIN command_receipts AS receipts
                ON receipts.result_entity_id = details.job_id
            """
        ).fetchone()
        endpoint_rows = connection.execute(
            """
            SELECT
                bindings.role,
                bindings.ordinal,
                revisions.root_uri,
                bindings.registration_state
            FROM standard_backup_job_endpoint_bindings AS bindings
            INNER JOIN endpoint_revisions AS revisions
                ON revisions.endpoint_id = bindings.endpoint_id
                AND revisions.id = bindings.endpoint_revision_id
            ORDER BY bindings.ordinal
            """
        ).fetchall()
        endpoint_count = connection.execute("SELECT count(*) FROM endpoints").fetchone()
        root_claim_count = connection.execute(
            "SELECT count(*) FROM endpoint_root_claims"
        ).fetchone()

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["created"] is True
    endpoint_bindings = gui_response["payload"]["endpoint_bindings"]
    assert endpoint_bindings["source"]["registration_state"] == "READ_ONLY_READY"
    assert endpoint_bindings["targets"][0]["registration_state"] == "REGISTRATION_PENDING"
    assert gui_response["payload"]["endpoint_classification_refresh"]["completed"] is True
    assert host_events[0]["host_status"]["mutations_enabled"] is True
    assert host_events[-1]["served_requests"] == 2
    assert persisted is not None
    assert persisted[0] == str(source_root)
    assert json.loads(persisted[1])[0]["path_label"] == str(target_root)
    assert persisted[2] == "SUCCEEDED"
    assert endpoint_rows == [
        (
            "SOURCE",
            0,
            endpoint_bindings["source"]["root_uri"],
            "READ_ONLY_READY",
        ),
        (
            "TARGET",
            1,
            endpoint_bindings["targets"][0]["root_uri"],
            "REGISTRATION_PENDING",
        ),
    ]
    assert endpoint_count == (2,)
    assert root_claim_count == (2,)
    assert not (source_root / ".mediasync").exists()
    assert not (target_root / ".mediasync").exists()


def test_gui_can_disconnect_and_reconnect_without_stopping_engine_host() -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-reconnect-test",
        suffix=uuid4().hex,
    )
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "4",
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        first_gui = _run_status_query(pipe_name)
        assert host.poll() is None
        second_gui = _run_status_query(pipe_name)
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    first_response = json.loads(first_gui.stdout)
    second_response = json.loads(second_gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert first_response["status"] == "ACCEPTED"
    assert second_response["status"] == "ACCEPTED"
    assert first_response["payload"]["host_status"]["role"] == "engine-host"
    assert second_response["payload"]["host_status"]["role"] == "engine-host"
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[-1]["served_requests"] == 4


def test_launcher_local_preview_host_publishes_persistent_engine_host(tmp_path: Path) -> None:
    installation_id = f"launcher-host-{uuid4().hex}"
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "scripts/run_role.py",
            "--role",
            "launcher",
            "--local-preview-host",
            "--installation-id",
            installation_id,
            "--state-root",
            str(state_root),
            "--run-executor-cycle-interval-ms",
            "1000",
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout = ""
    stderr = ""
    gui: subprocess.CompletedProcess[str] | None = None
    try:
        _wait_for_file(state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME)
        gui = _run_local_preview_status_query_when_ready(
            installation_id=installation_id,
            state_root=state_root,
        )
        assert host.poll() is None
        time.sleep(0.5)
    finally:
        if host.poll() is None:
            host.kill()
        stdout, stderr = host.communicate(timeout=5)

    assert gui is not None
    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["host_status"]["role"] == "engine-host"
    assert host_events[0]["event"] == "ENGINE_HOST_PIPE_STARTING"
    assert host_events[0]["serve_forever"] is True
    assert host_events[0]["run_executor_cycle_after_request"] is True
    assert host_events[0]["run_executor_cycle_interval_ms"] == 1000
    assert host_events[0]["host_locator"]["installation_id"] == installation_id
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[1]["event"] == "ENGINE_HOST_RUN_EXECUTOR_CYCLE"
    assert host_events[1]["run_executor_cycle"]["stopped_reason"] == "IDLE"


def test_launcher_local_preview_status_starts_host_and_queries_gui() -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="launcher-preview-status-test",
        suffix=uuid4().hex,
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "launcher",
            "--local-preview-status",
            "--pipe-name",
            pipe_name,
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    payload = json.loads(result.stdout)
    host_events = payload["engine_host"]["events"]

    assert result.stderr == ""
    assert payload["accepted"] is True
    assert payload["event"] == "LAUNCHER_LOCAL_PREVIEW_STATUS"
    assert payload["pipe_name"] == pipe_name
    assert payload["scope"] == "0B_SAME_USER_LOCAL_PREVIEW"
    assert payload["gui"]["returncode"] == 0
    assert payload["gui"]["response"]["status"] == "ACCEPTED"
    assert payload["gui"]["response"]["payload"]["host_status"]["role"] == "engine-host"
    assert payload["engine_host"]["returncode"] == 0
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[-1]["served_requests"] == 2
    assert payload["engine_host"]["killed"] is False


def test_launcher_local_preview_status_uses_host_locator_when_pipe_omitted(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "launcher",
            "--local-preview-status",
            "--installation-id",
            installation_id,
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    payload = json.loads(result.stdout)
    host_events = payload["engine_host"]["events"]
    publication_path = state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME
    publication = host_events[0]["host_locator"]

    assert result.stderr == ""
    assert payload["accepted"] is True
    assert payload["adoption_attempted"] is False
    assert payload["adopted_existing_host"] is False
    assert payload["stale_host_locator_publication_cleared"] is False
    assert payload["pipe_name"].startswith("MediaSyncHome-0B-")
    assert payload["host_locator"] == {
        "installation_id": installation_id,
        "locator_key": payload["pipe_name"].removeprefix("MediaSyncHome-0B-"),
        "mutex_name": f"Local\\{payload['pipe_name']}",
        "pipe_name": payload["pipe_name"],
        "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        "state_root": str(state_root),
    }
    assert payload["gui"]["response"]["status"] == "ACCEPTED"
    assert payload["engine_host"]["returncode"] == 0
    assert host_events[0]["pipe_name"] == payload["pipe_name"]
    assert publication["process_id"] > 0
    assert isinstance(publication["heartbeat_utc"], str)
    assert publication == {
        "heartbeat_utc": publication["heartbeat_utc"],
        "installation_id": installation_id,
        "locator_key": payload["pipe_name"].removeprefix("MediaSyncHome-0B-"),
        "mutex_name": f"Local\\{payload['pipe_name']}",
        "pipe_name": payload["pipe_name"],
        "process_id": publication["process_id"],
        "schema_version": 1,
        "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        "state_root": str(state_root),
        "status": "STARTING",
    }
    assert host_events[0]["host_mutex"] == {
        "acquired": True,
        "name": payload["host_locator"]["mutex_name"],
    }
    assert host_events[0]["host_locator"] == publication
    assert host_events[0]["host_locator_path"] == str(publication_path)
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2
    assert not publication_path.exists()


def test_launcher_local_preview_status_adopts_live_published_host(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"
    identity = win32_named_pipe.current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=state_root,
    )
    publication_path = state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            descriptor.pipe_name,
            "--serve-requests",
            "2",
            "--installation-id",
            installation_id,
            "--host-mutex-name",
            descriptor.mutex_name,
            "--publish-host-locator",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(publication_path)
        publication = json.loads(publication_path.read_text(encoding="utf-8"))
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "launcher",
                "--local-preview-status",
                "--installation-id",
                installation_id,
                "--state-root",
                str(state_root),
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    payload = json.loads(result.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert result.stderr == ""
    assert stderr == ""
    assert payload["accepted"] is True
    assert payload["adoption_attempted"] is True
    assert payload["adopted_existing_host"] is True
    assert payload["stale_host_locator_publication_cleared"] is False
    assert payload["pipe_name"] == descriptor.pipe_name
    assert payload["engine_host"] == {
        "events": [],
        "killed": False,
        "returncode": None,
        "stderr": "",
    }
    assert payload["gui"]["response"]["status"] == "ACCEPTED"
    assert payload["host_locator"] == descriptor.to_payload()
    assert payload["host_locator_publication"] == publication
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[0]["host_locator"] == publication
    assert host_events[-1]["served_requests"] == 2
    assert not publication_path.exists()


def test_launcher_local_preview_status_clears_stale_publication_before_fallback(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"
    identity = win32_named_pipe.current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=state_root,
    )
    stale_publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=state_root,
        process_id=os.getpid(),
    )
    publication_path = publish_local_engine_host_publication(stale_publication)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "launcher",
            "--local-preview-status",
            "--installation-id",
            installation_id,
            "--state-root",
            str(state_root),
            "--timeout-seconds",
            "3",
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )

    payload = json.loads(result.stdout)
    host_events = payload["engine_host"]["events"]
    final_publication = host_events[0]["host_locator"]

    assert result.stderr == ""
    assert payload["accepted"] is True
    assert payload["adoption_attempted"] is True
    assert payload["adopted_existing_host"] is False
    assert payload["stale_host_locator_publication_cleared"] is True
    assert payload["host_locator_publication"] == stale_publication.to_payload()
    assert host_events[0]["host_locator"]["process_id"] == final_publication["process_id"]
    assert final_publication["process_id"] != os.getpid()
    assert final_publication["pipe_name"] == descriptor.pipe_name
    assert host_events[-1]["served_requests"] == 2
    assert not publication_path.exists()


def test_gui_status_query_uses_host_locator_when_pipe_omitted(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"
    identity = win32_named_pipe.current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=state_root,
    )
    publication_path = state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            descriptor.pipe_name,
            "--serve-requests",
            "2",
            "--installation-id",
            installation_id,
            "--host-mutex-name",
            descriptor.mutex_name,
            "--publish-host-locator",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(publication_path)
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--query-status",
                "--installation-id",
                installation_id,
                "--state-root",
                str(state_root),
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert gui.stderr == ""
    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["host_status"]["role"] == "engine-host"
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[-1]["served_requests"] == 2


def test_trigger_status_query_uses_host_locator_when_pipe_omitted(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"
    identity = win32_named_pipe.current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=state_root,
    )
    publication_path = state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            descriptor.pipe_name,
            "--serve-requests",
            "2",
            "--installation-id",
            installation_id,
            "--host-mutex-name",
            descriptor.mutex_name,
            "--publish-host-locator",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_file(publication_path)
        trigger = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "trigger-client",
                "--query-status",
                "--installation-id",
                installation_id,
                "--state-root",
                str(state_root),
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    trigger_response = json.loads(trigger.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert trigger.stderr == ""
    assert stderr == ""
    assert trigger_response["status"] == "ACCEPTED"
    assert trigger_response["payload"]["host_status"]["role"] == "engine-host"
    assert [event["event"] for event in host_events] == [
        "ENGINE_HOST_PIPE_STARTING",
        "ENGINE_HOST_PIPE_STOPPED",
    ]
    assert host_events[-1]["served_requests"] == 2


def test_gui_status_query_preserves_live_unready_host_locator_when_pipe_omitted(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"
    identity = win32_named_pipe.current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=state_root,
    )
    stale_publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=state_root,
        process_id=os.getpid(),
    )
    publication_path = publish_local_engine_host_publication(stale_publication)

    gui = subprocess.run(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "gui",
            "--query-status",
            "--installation-id",
            installation_id,
            "--state-root",
            str(state_root),
            "--timeout-seconds",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    gui_response = json.loads(gui.stdout)

    assert gui.stderr == ""
    assert gui.returncode == 2
    assert gui_response == {
        "payload": {
            "host_locator_publication": stale_publication.to_payload(),
            "reason": "HOST_LOCATOR_PUBLICATION_NOT_LIVE",
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
            "stale_host_locator_publication_cleared": False,
        },
        "reason": "ENGINE_HOST_UNAVAILABLE",
        "status": "REJECTED",
    }
    assert publication_path.exists()


def test_trigger_status_query_preserves_live_unready_host_locator_when_pipe_omitted(
    tmp_path: Path,
) -> None:
    installation_id = f"preview-{uuid4().hex}"
    state_root = tmp_path / "state"
    identity = win32_named_pipe.current_process_identity()
    descriptor = build_local_engine_host_descriptor_for_user(
        installation_id=installation_id,
        user_scope_hash=identity.user_sid_hash,
        state_root=state_root,
    )
    stale_publication = build_local_engine_host_publication(
        installation_id=descriptor.installation_id,
        pipe_name=descriptor.pipe_name,
        mutex_name=descriptor.mutex_name,
        state_root=state_root,
        process_id=os.getpid(),
    )
    publication_path = publish_local_engine_host_publication(stale_publication)

    trigger = subprocess.run(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "trigger-client",
            "--query-status",
            "--installation-id",
            installation_id,
            "--state-root",
            str(state_root),
            "--timeout-seconds",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    trigger_response = json.loads(trigger.stdout)

    assert trigger.stderr == ""
    assert trigger.returncode == 2
    assert trigger_response == {
        "payload": {
            "host_locator_publication": stale_publication.to_payload(),
            "reason": "HOST_LOCATOR_PUBLICATION_NOT_LIVE",
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
            "stale_host_locator_publication_cleared": False,
        },
        "reason": "ENGINE_HOST_UNAVAILABLE",
        "status": "REJECTED",
    }
    assert publication_path.exists()


def test_engine_host_mutex_rejects_when_same_user_mutex_is_owned(tmp_path: Path) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-mutex-reject-test",
        suffix=uuid4().hex,
    )
    mutex_name = f"Local\\MediaSyncHome-0B-{uuid4().hex[:24]}"
    state_root = tmp_path / "state"
    mutex = LocalEngineHostMutex.acquire(mutex_name)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "engine-host",
                "--pipe-name",
                pipe_name,
                "--serve-requests",
                "1",
                "--host-mutex-name",
                mutex_name,
                "--publish-host-locator",
                "--state-root",
                str(state_root),
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        mutex.close()

    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    assert result.stderr == ""
    assert result.returncode == 3
    assert events == [
        {
            "event": "ENGINE_HOST_SINGLETON_REJECTED",
            "mutex_name": mutex_name,
            "pipe_name": pipe_name,
            "reason": "ENGINE_HOST_ALREADY_RUNNING",
            "scope": "0B_SAME_USER_LOCAL_PREVIEW",
        }
    ]
    assert not (state_root / LOCAL_ENGINE_HOST_PUBLICATION_FILENAME).exists()


def test_engine_host_state_root_persists_gui_submitted_disabled_command(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-state-root-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    idempotency_key = "66666666-6666-4666-8666-666666666666"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--submit-command",
                "UNKNOWN_MUTATION",
                "--request-id",
                "44444444-4444-4444-8444-444444444444",
                "--idempotency-key",
                idempotency_key,
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    with sqlite3.connect(state_root / "catalog.sqlite") as connection:
        row = connection.execute(
            """
            SELECT state, rejection_reason
            FROM command_receipts
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()

    assert stderr == ""
    assert gui.returncode == 2
    assert gui_response["status"] == "REJECTED"
    assert gui_response["reason"] == "MUTATING_COMMANDS_DISABLED"
    assert gui_response["payload"]["receipt"]["state"] == "REJECTED"
    assert row == ("REJECTED", "MUTATING_COMMANDS_DISABLED")
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[0]["startup_reconciliation"]["command_receipts"]["scanned"] == 0
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_persists_trigger_occurrence_receipt(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-trigger-occurrence-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    delivery_id = "11111111-1111-4111-8111-111111111111"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        trigger = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "trigger-client",
                "--pipe-name",
                pipe_name,
                "--enqueue-trigger-occurrence",
                "--schedule-id",
                "schedule-a",
                "--schedule-revision-hash",
                "a" * 64,
                "--delivery-id",
                delivery_id,
                "--observed-start-utc",
                "2026-07-20T12:00:00.000Z",
                "--task-definition-hash",
                "b" * 64,
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    trigger_response = json.loads(trigger.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    with sqlite3.connect(state_root / "catalog.sqlite") as connection:
        row = connection.execute(
            """
            SELECT command_name, state, rejection_reason
            FROM command_receipts
            WHERE idempotency_key = ?
            """,
            (delivery_id,),
        ).fetchone()

    assert stderr == ""
    assert trigger.returncode == 2
    assert trigger_response["status"] == "REJECTED"
    assert trigger_response["reason"] == "MUTATING_COMMANDS_DISABLED"
    assert trigger_response["payload"]["recognized"] is True
    assert trigger_response["payload"]["schedule_id"] == "schedule-a"
    assert trigger_response["payload"]["receipt"]["state"] == "REJECTED"
    assert row == ("ENQUEUE_TRIGGER_OCCURRENCE", "REJECTED", "MUTATING_COMMANDS_DISABLED")
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_backup_overview_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-backup-overview-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-backup-overview",
                "--limit",
                "5",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["backup_overview"]["read_model_available"] is True
    assert gui_response["payload"]["backup_overview"]["limit"] == 5
    assert gui_response["payload"]["backup_overview"]["jobs"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_backup_job_detail_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-backup-job-detail-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-backup-job-detail",
                "--job-id",
                "job-a",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["backup_job_detail"]["job_id"] == "job-a"
    assert gui_response["payload"]["backup_job_detail"]["read_model_available"] is True
    assert gui_response["payload"]["backup_job_detail"]["found"] is False
    assert gui_response["payload"]["backup_job_detail"]["job"] is None
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_activity_overview_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-activity-overview-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-activity-overview",
                "--limit",
                "5",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["activity_overview"]["read_model_available"] is True
    assert gui_response["payload"]["activity_overview"]["limit"] == 5
    assert gui_response["payload"]["activity_overview"]["runs"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_run_progress_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-run-progress-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-run-progress",
                "--run-id",
                "run-missing",
                "--after-sequence-no",
                "7",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["run_progress"]["read_model_available"] is True
    assert gui_response["payload"]["run_progress"]["run_found"] is False
    assert gui_response["payload"]["run_progress"]["requested_after_sequence_no"] == 7
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_plan_operations_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-plan-operations-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-plan-operations",
                "--plan-id",
                "plan-a",
                "--limit",
                "5",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["plan_operations"]["read_model_available"] is True
    assert gui_response["payload"]["plan_operations"]["limit"] == 5
    assert gui_response["payload"]["plan_operations"]["operations"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_plan_endpoints_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-plan-endpoints-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-plan-endpoints",
                "--plan-id",
                "plan-a",
                "--limit",
                "5",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["plan_endpoints"]["read_model_available"] is True
    assert gui_response["payload"]["plan_endpoints"]["limit"] == 5
    assert gui_response["payload"]["plan_endpoints"]["endpoints"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_snapshot_entries_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-snapshot-entries-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-snapshot-entries",
                "--snapshot-id",
                "snapshot-a",
                "--limit",
                "5",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["snapshot_entries"]["read_model_available"] is True
    assert gui_response["payload"]["snapshot_entries"]["limit"] == 5
    assert gui_response["payload"]["snapshot_entries"]["entries"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_cataloged_files_query(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-cataloged-files-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "2",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        gui = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-cataloged-files",
                "--run-id",
                "run-a",
                "--limit",
                "5",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    gui_response = json.loads(gui.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert gui_response["status"] == "ACCEPTED"
    assert gui_response["payload"]["cataloged_files"]["read_model_available"] is True
    assert gui_response["payload"]["cataloged_files"]["limit"] == 5
    assert gui_response["payload"]["cataloged_files"]["run_id"] == "run-a"
    assert gui_response["payload"]["cataloged_files"]["files"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 2


def test_engine_host_state_root_serves_snapshot_health_queries(
    tmp_path: Path,
) -> None:
    pipe_name = win32_named_pipe.make_pipe_name(
        installation_id="role-snapshot-health-test",
        suffix=uuid4().hex,
    )
    state_root = tmp_path / "state"
    host = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "engine-host",
            "--pipe-name",
            pipe_name,
            "--serve-requests",
            "4",
            "--state-root",
            str(state_root),
        ],
        cwd=Path(__file__).resolve().parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        coverage = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-snapshot-coverage",
                "--snapshot-id",
                "snapshot-a",
                "--limit",
                "5",
                "--coverage-state",
                "COMPLETE",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        issues = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--pipe-name",
                pipe_name,
                "--query-snapshot-issues",
                "--snapshot-id",
                "snapshot-a",
                "--limit",
                "5",
                "--blocking-only",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        stdout, stderr = host.communicate(timeout=10)
    finally:
        if host.poll() is None:
            host.kill()
            host.communicate(timeout=5)

    coverage_response = json.loads(coverage.stdout)
    issues_response = json.loads(issues.stdout)
    host_events = [json.loads(line) for line in stdout.splitlines() if line.strip()]

    assert stderr == ""
    assert coverage_response["status"] == "ACCEPTED"
    assert coverage_response["payload"]["snapshot_coverage"]["read_model_available"] is True
    assert coverage_response["payload"]["snapshot_coverage"]["limit"] == 5
    assert coverage_response["payload"]["snapshot_coverage"]["coverage_states"] == ["COMPLETE"]
    assert coverage_response["payload"]["snapshot_coverage"]["coverage"] == []
    assert issues_response["status"] == "ACCEPTED"
    assert issues_response["payload"]["snapshot_issues"]["read_model_available"] is True
    assert issues_response["payload"]["snapshot_issues"]["limit"] == 5
    assert issues_response["payload"]["snapshot_issues"]["blocking_only"] is True
    assert issues_response["payload"]["snapshot_issues"]["issues"] == []
    assert host_events[0]["state_root"] == str(state_root)
    assert host_events[-1]["served_requests"] == 4


def _run_status_query(pipe_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/run_role.py",
            "--role",
            "gui",
            "--pipe-name",
            pipe_name,
            "--query-status",
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )


def _run_local_preview_status_query_when_ready(
    *,
    installation_id: str,
    state_root: Path,
    timeout_seconds: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    deadline = time.monotonic() + timeout_seconds
    last_result: subprocess.CompletedProcess[str] | None = None
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_role.py",
                "--role",
                "gui",
                "--installation-id",
                installation_id,
                "--state-root",
                str(state_root),
                "--query-status",
                "--timeout-seconds",
                "0.2",
            ],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        last_result = result
        if result.returncode == 0:
            try:
                response = json.loads(result.stdout)
            except json.JSONDecodeError:
                response = None
            if isinstance(response, dict) and response.get("status") == "ACCEPTED":
                return result
        time.sleep(0.05)
    assert last_result is not None
    raise AssertionError(
        "timed out waiting for local-preview host status; "
        f"returncode={last_result.returncode}; "
        f"stdout={last_result.stdout!r}; stderr={last_result.stderr!r}"
    )


def _wait_for_file(path: Path, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")
