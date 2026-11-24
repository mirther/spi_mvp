from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from app.services.hpc import execution
from app.services.hpc.storage import get_run_storage_paths


def test_run_request_uses_saved_graph_execution_order():
    nodes = [
        SimpleNamespace(id="in", module_id="segyin", parameters={"path": "storage/data/input.sgy"}),
        SimpleNamespace(id="sort", module_id="sort", parameters={"key": "cdp"}),
        SimpleNamespace(id="out", module_id="segyout", parameters={"path": "storage/data/out.sgy"}),
    ]
    connections = [
        SimpleNamespace(source_node_id="in", target_node_id="out"),
        SimpleNamespace(source_node_id="out", target_node_id="sort"),
    ]

    request = execution.build_run_request(
        project_id="project-1",
        run_id="run-1",
        project_name="Test project",
        execution_order=["in", "out", "sort"],
        nodes=nodes,
        connections=connections,
    )

    assert [step["node_id"] for step in request["modules"]] == ["in", "out", "sort"]
    assert request["execution_mode"] == "all"
    assert request["modules"][0]["parameters"]["path"] == "storage/data/input.sgy"


def test_write_remote_text_base64_encodes_content(monkeypatch):
    commands = []
    monkeypatch.setattr(execution, "run_ssh", lambda command: commands.append(command) or "")

    execution.write_remote_text(PurePosixPath("/tmp/spi/request.json"), '{"name":"SPI"}')

    assert len(commands) == 1
    assert "base64 -d" in commands[0]
    assert '{"name":"SPI"}' not in commands[0]
    assert "chmod 600" in commands[0]


def test_submit_run_creates_files_and_parses_sbatch_id(monkeypatch):
    events = []
    monkeypatch.setattr(execution.settings, "hpc_host", "headnode.example")
    monkeypatch.setattr(execution.settings, "hpc_user", "spiuser")
    monkeypatch.setattr(execution, "ensure_remote_run_directory", lambda paths: events.append(("mkdir", paths.directory)))
    monkeypatch.setattr(execution, "write_remote_text", lambda path, content: events.append((str(path), content)))
    monkeypatch.setattr(execution, "run_ssh", lambda command: events.append(("sbatch", command)) or "987654;cluster")

    paths = get_run_storage_paths("project-1", "run-1")
    config = SimpleNamespace(
        job_name="spi_job", partition="a100serv", nodes=1, tasks=1,
        cpus=36, time="12:00:00", memory="300G",
    )
    request = {"schema_version": 1, "modules": []}

    job_id = execution.submit_run(paths, config, request)

    assert job_id == "987654"
    assert any(str(paths.request_file) in str(event[0]) for event in events if isinstance(event, tuple))
    assert any(str(paths.batch_file) in str(event[0]) for event in events if isinstance(event, tuple))


def test_submit_run_rejects_unexpected_sbatch_response(monkeypatch):
    monkeypatch.setattr(execution.settings, "hpc_host", "headnode.example")
    monkeypatch.setattr(execution.settings, "hpc_user", "spiuser")
    monkeypatch.setattr(execution, "ensure_remote_run_directory", lambda paths: None)
    monkeypatch.setattr(execution, "write_remote_text", lambda path, content: None)
    monkeypatch.setattr(execution, "run_ssh", lambda command: "Submitted batch job 123")

    paths = get_run_storage_paths("project-1", "run-1")
    config = SimpleNamespace(
        job_name="spi_job", partition="a100serv", nodes=1, tasks=1,
        cpus=36, time="12:00:00", memory="300G",
    )
    with pytest.raises(execution.HPCExecutionError):
        execution.submit_run(paths, config, {"schema_version": 1})


def test_slurm_status_reads_squeue_then_sacct(monkeypatch):
    responses = iter(["", "COMPLETED|0:0"])
    monkeypatch.setattr(execution, "run_ssh", lambda command: next(responses))
    assert execution.get_slurm_job_status("123456") == "Completed"


def test_slurm_status_maps_pending(monkeypatch):
    monkeypatch.setattr(execution, "run_ssh", lambda command: "PENDING")
    assert execution.get_slurm_job_status("123456") == "Queued"


def test_slurm_status_rejects_non_numeric_job_id():
    with pytest.raises(ValueError):
        execution.get_slurm_job_status("123; rm -rf /")


def test_normal_ssh_commands_require_an_active_hpc_lease(monkeypatch):
    from app.services.hpc import access, ssh

    def deny_access():
        raise access.HPCAccessError("HPC access lease expired. Reconnect to HPC.", 503)

    monkeypatch.setattr(access, "assert_hpc_access_active", deny_access)
    monkeypatch.setattr(
        ssh.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("SSH must not run without an active HPC lease"),
    )

    with pytest.raises(ssh.HPCConnectionError, match="lease expired"):
        ssh.run_ssh("hostname")
