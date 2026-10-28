"""Integration tests proving volume data persistence across container removal."""

from __future__ import annotations

import contextlib
import uuid

import pytest

from dbrescue.docker_manager import DockerManager


@pytest.fixture
def docker_mgr():
    mgr = DockerManager()
    if not mgr.is_docker_available():
        pytest.skip("Docker daemon is offline. Skipping live volume persistence integration test.")
    return mgr


@pytest.mark.integration
def test_volume_persistence_across_container_removal(docker_mgr):
    """Proves that data written to a named volume survives container removal.

    SAFETY RULE: Uses a disposable volume named 'dbrescue_test_*'; never touches 'mysql_data'.
    """
    client = docker_mgr._get_client()

    # 1. Create disposable test volume with unique suffix
    test_id = uuid.uuid4().hex[:8]
    disposable_vol_name = f"dbrescue_test_vol_{test_id}"
    disposable_container_1 = f"dbrescue-mysql-sandbox-test1-{test_id}"
    disposable_container_2 = f"dbrescue-mysql-sandbox-test2-{test_id}"

    volume = client.volumes.create(name=disposable_vol_name)

    try:
        # 2. Launch Container 1 and write a verification file into the volume
        client.containers.run(
            image="alpine:3.20",
            name=disposable_container_1,
            command=["sh", "-c", "echo 'PERSISTENT_DATA_12345' > /data/persistence.txt"],
            volumes={disposable_vol_name: {"bind": "/data", "mode": "rw"}},
            remove=False,
        )

        # 3. Safely remove Container 1 with v=False (preserving volume)
        docker_mgr.remove_container(disposable_container_1, force=True)

        # 4. Launch Container 2 mounting the SAME volume and read the file
        output = client.containers.run(
            image="alpine:3.20",
            name=disposable_container_2,
            command=["cat", "/data/persistence.txt"],
            volumes={disposable_vol_name: {"bind": "/data", "mode": "ro"}},
            remove=False,
        )

        read_content = output.decode("utf-8").strip()
        assert read_content == "PERSISTENT_DATA_12345", "Data must survive container removal!"

        # 5. Clean up Container 2
        docker_mgr.remove_container(disposable_container_2, force=True)

    finally:
        # 6. Cleanup disposable test volume only
        with contextlib.suppress(Exception):
            volume.remove(force=True)
