from types import SimpleNamespace

import pytest

from app.services.pipeline_order import get_execution_order
from app.services.hpc.storage import (
    build_sbatch_script,
    get_run_storage_paths,
    validate_segy_filename,
    validate_segy_relative_path,
)


def test_execution_order_follows_dependencies_and_is_stable():
    node_ids = ["segyin", "sort", "segyout"]
    connections = [("segyin", "segyout"), ("segyout", "sort")]
    assert get_execution_order(node_ids, connections) == ["segyin", "segyout", "sort"]


def test_independent_nodes_keep_insertion_order():
    assert get_execution_order(["first", "second", "third"], []) == [
        "first", "second", "third"
    ]


def test_execution_order_rejects_cycle():
    with pytest.raises(ValueError):
        get_execution_order(["a", "b"], [("a", "b"), ("b", "a")])


def test_storage_accepts_only_direct_segy_basenames():
    assert validate_segy_filename("survey_01.sgy") == "survey_01.sgy"
    assert validate_segy_relative_path("storage/data/survey_01.sgy") == (
        "storage/data/survey_01.sgy"
    )

    for unsafe in (
        "/etc/passwd",
        "other/survey.sgy",
        "storage/data/../survey.sgy",
        "storage/data/subdir/survey.sgy",
        "storage/data/file.txt",
    ):
        with pytest.raises(ValueError):
            validate_segy_relative_path(unsafe)


def test_sbatch_logs_are_spi_managed_per_run():
    config = SimpleNamespace(
        job_name="swi_jup",
        partition="a100serv",
        nodes=1,
        tasks=1,
        cpus=36,
        time="12:00:00",
        memory="300G",
    )
    paths = get_run_storage_paths("project-01", "run-02")
    script = build_sbatch_script(
        config,
        paths,
        ["/usr/bin/python3", "/home/user/spi/workers/runner.py", "--request", str(paths.request_file)],
    )

    assert f"#SBATCH --output={paths.stdout_pattern}" in script
    assert f"#SBATCH --error={paths.stderr_pattern}" in script
    assert "swi_jup" in script
    assert "a100serv" in script


def test_run_paths_reject_traversal_ids():
    with pytest.raises(ValueError):
        get_run_storage_paths("../other", "run-1")
