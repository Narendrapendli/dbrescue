"""Unit tests for dbrescue.docker_manager and volume_inspector using mocks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dbrescue.docker_manager import (
    ContainerInfo,
    DockerManager,
    VolumeMisMounted,
    VolumeMissing,
)
from dbrescue.security import SecurityValidationError
from dbrescue.volume_inspector import VolumeInspector


@pytest.fixture
def mock_docker_client():
    """Provides a mocked DockerClient instance."""
    client = MagicMock()
    client.ping.return_value = True
    return client


@pytest.fixture
def docker_manager(mock_docker_client):
    return DockerManager(client=mock_docker_client)


@pytest.fixture
def volume_inspector(docker_manager):
    return VolumeInspector(docker_manager=docker_manager)


# --- Docker Manager Unit Tests ---


@pytest.mark.unit
def test_is_docker_available_true(docker_manager, mock_docker_client):
    """Asserts is_docker_available returns True when ping succeeds."""
    mock_docker_client.ping.return_value = True
    assert docker_manager.is_docker_available() is True


@pytest.mark.unit
def test_is_docker_available_false_on_exception():
    """Asserts is_docker_available returns False when daemon is offline."""
    mgr = DockerManager()
    with patch("docker.from_env", side_effect=Exception("Connection refused")):
        assert mgr.is_docker_available() is False


@pytest.mark.unit
def test_remove_container_enforces_v_false(docker_manager, mock_docker_client):
    """CRITICAL SAFETY TEST: Asserts container.remove(v=False) is strictly called to preserve volumes."""
    mock_container = MagicMock()
    mock_container.name = "dbrescue-mysql-primary"
    mock_docker_client.containers.get.return_value = mock_container

    result = docker_manager.remove_container("dbrescue-mysql-primary", force=True)
    assert result is True

    # Assert v=False was passed to prevent volume removal!
    mock_container.remove.assert_called_once_with(v=False, force=True)


@pytest.mark.unit
def test_stop_container_success(docker_manager, mock_docker_client):
    """Asserts container stop initiates graceful shutdown with timeout."""
    mock_container = MagicMock()
    mock_container.status = "running"
    mock_docker_client.containers.get.return_value = mock_container

    assert docker_manager.stop_container("dbrescue-mysql-primary", timeout_sec=10) is True
    mock_container.stop.assert_called_once_with(timeout=10)


@pytest.mark.unit
def test_recreate_container_sets_127_0_0_1_port_binding(docker_manager, mock_docker_client):
    """Verifies that recreated containers bind exclusively to 127.0.0.1 on the host."""
    mock_container = MagicMock()
    mock_container.name = "dbrescue-mysql-primary"
    mock_container.id = "abcdef123456789"
    mock_container.attrs = {
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {"Image": "mysql:8.4.3"},
        "Created": "2026-10-28T22:30:00Z",
        "NetworkSettings": {"Ports": {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3306"}]}},
    }
    mock_docker_client.containers.get.return_value = mock_container
    mock_docker_client.containers.run.return_value = mock_container

    info = docker_manager.recreate_container_from_compose_spec(
        container_name="dbrescue-mysql-primary",
        volume_name="dbrescue_mysql_data",
        host_port=3306,
        image="mysql:8.4.3",
    )

    assert isinstance(info, ContainerInfo)
    assert info.ports.get("3306/tcp") == "127.0.0.1:3306"

    # Verify run kwargs
    call_kwargs = mock_docker_client.containers.run.call_args[1]
    assert call_kwargs["ports"] == {"3306/tcp": ("127.0.0.1", 3306)}
    assert call_kwargs["image"] == "mysql:8.4.3"


@pytest.mark.unit
def test_exec_in_container_allowlist_enforcement(docker_manager, mock_docker_client):
    """Asserts allowlisted commands execute and unauthorized commands raise SecurityValidationError."""
    mock_container = MagicMock()
    mock_container.exec_run.return_value = (0, b"mysqld is alive\n")
    mock_docker_client.containers.get.return_value = mock_container

    # 1. Allowed commands
    code, output = docker_manager.exec_in_container(
        "dbrescue-mysql-primary", ["mysqladmin", "ping"]
    )
    assert code == 0
    assert "mysqld is alive" in output

    code2, _ = docker_manager.exec_in_container(
        "dbrescue-mysql-primary", ["mysqldump", "--all-databases"]
    )
    assert code2 == 0

    # 2. Unauthorized commands MUST raise SecurityValidationError
    forbidden_commands = [
        ["cat", "/etc/shadow"],
        ["sh", "-c", "rm -rf /"],
        ["curl", "http://malicious-site.com"],
        ["bash"],
    ]
    for bad_cmd in forbidden_commands:
        with pytest.raises(SecurityValidationError, match="Security Violation"):
            docker_manager.exec_in_container("dbrescue-mysql-primary", bad_cmd)


# --- Volume Inspector Unit Tests ---


@pytest.mark.unit
def test_inspect_volume_found(volume_inspector, mock_docker_client):
    """Asserts inspect_volume returns VolumeInfo when named volume exists."""
    mock_vol = MagicMock()
    mock_vol.name = "dbrescue_mysql_data"
    mock_vol.attrs = {
        "Driver": "local",
        "Mountpoint": "/var/lib/docker/volumes/dbrescue_mysql_data/_data",
        "Labels": {"project": "dbrescue"},
        "CreatedAt": "2026-10-28T22:30:00Z",
    }
    mock_docker_client.volumes.get.return_value = mock_vol
    mock_docker_client.df.return_value = {
        "Volumes": [{"Name": "dbrescue_mysql_data", "UsageData": {"Size": 104857600}}]
    }

    info = volume_inspector.inspect_volume("dbrescue_mysql_data")
    assert info.name == "dbrescue_mysql_data"
    assert info.driver == "local"
    assert info.size_bytes == 104857600
    assert "100.00 MB" in info.size_display


@pytest.mark.unit
def test_inspect_volume_missing_raises_volume_missing(volume_inspector, mock_docker_client):
    """Asserts inspect_volume raises VolumeMissing when volume does not exist."""
    from docker.errors import NotFound

    mock_docker_client.volumes.get.side_effect = NotFound("Volume not found")

    with pytest.raises(VolumeMissing, match="does not exist"):
        volume_inspector.inspect_volume("dbrescue_mysql_data")


@pytest.mark.unit
def test_inspect_volume_size_unknown_when_not_obtainable(volume_inspector, mock_docker_client):
    """Asserts volume size shows 'UNKNOWN' (not 0) when Docker daemon cannot provide size."""
    mock_vol = MagicMock()
    mock_vol.name = "dbrescue_mysql_data"
    mock_vol.attrs = {"Driver": "local", "Mountpoint": "/mnt/data", "Labels": {}}
    mock_docker_client.volumes.get.return_value = mock_vol
    # Docker df returns empty volumes list
    mock_docker_client.df.return_value = {"Volumes": []}

    size_b, size_display = volume_inspector.get_volume_size_bytes("dbrescue_mysql_data")
    assert size_b is None
    assert size_display == "UNKNOWN"


@pytest.mark.unit
def test_inspect_container_mounts_detects_bind_mount_violation(
    volume_inspector, mock_docker_client
):
    """Asserts that a host bind mount at /var/lib/mysql triggers VolumeMisMounted error."""
    mock_container = MagicMock()
    mock_container.name = "dbrescue-mysql-primary"
    mock_container.attrs = {
        "Mounts": [
            {
                "Type": "bind",  # VIOLATION: Should be 'volume'
                "Source": "C:\\Host\\MySQL\\Data",
                "Destination": "/var/lib/mysql",
            }
        ]
    }
    mock_docker_client.containers.get.return_value = mock_container

    with pytest.raises(VolumeMisMounted, match="mounts '/var/lib/mysql' as a host bind mount"):
        volume_inspector.inspect_container_mounts("dbrescue-mysql-primary")


@pytest.mark.unit
def test_inspect_container_mounts_detects_anonymous_or_wrong_volume(
    volume_inspector, mock_docker_client
):
    """Asserts that an unauthorized or anonymous volume triggers VolumeMisMounted error."""
    mock_container = MagicMock()
    mock_container.name = "dbrescue-mysql-primary"
    mock_container.attrs = {
        "Mounts": [
            {
                "Type": "volume",
                "Name": "7a8b9c0d1e2f3a4b_anonymous_vol",  # VIOLATION: Not the approved named volume
                "Destination": "/var/lib/mysql",
            }
        ]
    }
    mock_docker_client.containers.get.return_value = mock_container

    with pytest.raises(VolumeMisMounted, match="unauthorized or anonymous volume"):
        volume_inspector.inspect_container_mounts(
            "dbrescue-mysql-primary", expected_volume_name="dbrescue_mysql_data"
        )


@pytest.mark.unit
def test_inspect_container_mounts_success(volume_inspector, mock_docker_client):
    """Asserts successful verification when correct named volume is mounted at /var/lib/mysql."""
    mock_container = MagicMock()
    mock_container.name = "dbrescue-mysql-primary"
    mock_container.attrs = {
        "Mounts": [
            {
                "Type": "volume",
                "Name": "dbrescue_mysql_data",
                "Destination": "/var/lib/mysql",
            }
        ]
    }
    mock_docker_client.containers.get.return_value = mock_container

    res = volume_inspector.inspect_container_mounts(
        "dbrescue-mysql-primary",
        expected_volume_name="dbrescue_mysql_data",
        expected_mount_path="/var/lib/mysql",
    )
    assert res.is_correct is True
    assert res.is_named_volume is True
    assert res.source_name == "dbrescue_mysql_data"
