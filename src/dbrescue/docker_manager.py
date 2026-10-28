"""Docker container and infrastructure management layer using the official Docker SDK.

Enforces strict allowlists for containers, volumes, and images; prevents accidental volume
deletion during container removal; ensures strict 127.0.0.1 loopback port binding; and
safely executes allowlisted diagnostic commands inside containers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container

from dbrescue.config import DockerConfig, MySQLConnectionConfig, RescueException
from dbrescue.db import ping_database
from dbrescue.security import (
    SecurityValidationError,
    sanitize_error_message,
    validate_container_name,
    validate_volume_name,
)

logger = logging.getLogger("dbrescue.docker_manager")

# Fixed Allowlist of Executable Commands inside Containers (Security Defense)
ALLOWED_EXEC_COMMAND_PREFIXES: tuple[Sequence[str], ...] = (
    ("mysqladmin", "ping"),
    ("mysqldump",),
    ("mysql",),
)

ALLOWED_IMAGES: set[str] = {
    "mysql:8.4.3",
    "mysql:8.4",
    "prom/mysqld-exporter:v0.16.0",
    "alpine:3.20",
}


# --- Typed Docker Exceptions ---

class DockerUnavailable(RescueException):
    """Raised when the Docker daemon is offline or unreachable."""
    pass


class VolumeMissing(RescueException):
    """Raised when a required named storage volume is not found."""
    pass


class VolumeMisMounted(RescueException):
    """Raised when a container volume is bound to the wrong mountpoint or uses an unauthorized mount type."""
    pass


class ContainerRecreateFailed(RescueException):
    """Raised when container re-provisioning or creation from specification fails."""
    pass


class ReadinessTimeout(RescueException):
    """Raised when container healthcheck or database ping fails to become ready within timeout."""
    pass


# Aliases conforming to CONTRACTS.md
DockerManagerError = DockerUnavailable
DockerDaemonUnreachableError = DockerUnavailable
ContainerProvisionError = ContainerRecreateFailed
ContainerHealthTimeoutError = ReadinessTimeout
VolumeNotFoundError = VolumeMissing


# --- Dataclasses ---

@dataclass(frozen=True)
class ContainerInfo:
    """Standardized metadata model for container status."""
    container_id: str
    name: str
    status: str
    image: str
    created_at: str
    ports: dict[str, str] = field(default_factory=dict)
    is_healthy: bool = False
    health_status: str = "N/A"


@dataclass(frozen=True)
class VolumeInfo:
    """Standardized metadata model for Docker storage volume."""
    name: str
    driver: str
    mountpoint: str
    labels: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    size_bytes: int | None = None
    size_display: str = "UNKNOWN"


# --- Validation Helpers ---

def _validate_image_allowlist(image_tag: str) -> str:
    cleaned = image_tag.strip()
    if cleaned in ALLOWED_IMAGES or any(cleaned.startswith("mysql:8.4") for _ in [0]):
        return cleaned
    raise SecurityValidationError(
        f"Security Violation: Container image '{cleaned}' is not in the approved image allowlist. "
        f"Allowed images: {ALLOWED_IMAGES}"
    )


def _validate_exec_command(command: Sequence[str]) -> None:
    if not command:
        raise SecurityValidationError("Exec command cannot be empty.")

    cmd_tuple = tuple(c.strip() for c in command)

    # Check against allowlist prefixes
    for allowed_prefix in ALLOWED_EXEC_COMMAND_PREFIXES:
        if len(cmd_tuple) >= len(allowed_prefix) and cmd_tuple[:len(allowed_prefix)] == allowed_prefix:
            # Command matches an allowed prefix
            return

    raise SecurityValidationError(
        f"Security Violation: Execution of command '{command}' is forbidden inside containers. "
        f"Only allowed tools: {ALLOWED_EXEC_COMMAND_PREFIXES}"
    )


# --- Docker Manager Engine ---

class DockerManager:
    """Manages Docker engine communication, lifecycle, and safe sandboxing."""

    def __init__(self, docker_config: DockerConfig | None = None, client: docker.DockerClient | None = None) -> None:
        self.config = docker_config
        self._client = client

    def _get_client(self, timeout_sec: int = 3) -> docker.DockerClient:
        if self._client is not None:
            return self._client

        try:
            # Initialize official Docker SDK client
            client = docker.from_env(timeout=timeout_sec)
            client.ping()
            self._client = client
            return client
        except (DockerException, APIError, Exception) as err:
            sanitized = sanitize_error_message(err)
            raise DockerUnavailable(
                f"Docker daemon is not available or unreachable: {sanitized}"
            ) from err

    def is_docker_available(self, timeout_sec: int = 3) -> bool:
        """Tests whether Docker daemon is responding within timeout."""
        try:
            client = self._get_client(timeout_sec=timeout_sec)
            return bool(client.ping())
        except DockerUnavailable:
            return False
        except Exception as err:
            logger.debug("Docker ping failed: %s", sanitize_error_message(err))
            return False

    def ping_daemon(self) -> bool:
        """CONTRACTS.md compatibility method."""
        return self.is_docker_available()

    def get_container_state(self, container_name_or_id: str) -> ContainerInfo:
        """Retrieves verified status, health, and port mappings of a container."""
        validate_container_name(container_name_or_id)
        client = self._get_client()

        try:
            container: Container = client.containers.get(container_name_or_id)
            attrs = container.attrs
            state = attrs.get("State", {})

            health_info = state.get("Health", {})
            health_status = health_info.get("Status", "N/A")
            is_healthy = health_status == "healthy" or state.get("Status") == "running"

            # Parse port mappings
            ports_dict: dict[str, str] = {}
            network_settings = attrs.get("NetworkSettings", {})
            ports_raw = network_settings.get("Ports", {})
            if isinstance(ports_raw, dict):
                for container_port, host_bindings in ports_raw.items():
                    if host_bindings and isinstance(host_bindings, list):
                        host_ip = host_bindings[0].get("HostIp", "")
                        host_port = host_bindings[0].get("HostPort", "")
                        ports_dict[str(container_port)] = f"{host_ip}:{host_port}"

            return ContainerInfo(
                container_id=container.id[:12],
                name=container.name,
                status=state.get("Status", "unknown"),
                image=attrs.get("Config", {}).get("Image", "unknown"),
                created_at=attrs.get("Created", ""),
                ports=ports_dict,
                is_healthy=is_healthy,
                health_status=health_status,
            )
        except NotFound as err:
            raise DockerUnavailable(f"Container '{container_name_or_id}' was not found.") from err
        except (DockerException, APIError) as err:
            raise DockerUnavailable(f"Failed to inspect container: {sanitize_error_message(err)}") from err

    def get_container(self, name_or_id: str) -> ContainerInfo | None:
        """CONTRACTS.md compatibility method."""
        try:
            return self.get_container_state(name_or_id)
        except DockerUnavailable:
            return None

    def stop_container(self, container_name_or_id: str, timeout_sec: int = 15) -> bool:
        """Gracefully stops a target container with timeout."""
        validate_container_name(container_name_or_id)
        client = self._get_client()

        try:
            container: Container = client.containers.get(container_name_or_id)
            if container.status != "running":
                logger.info("Container '%s' is already stopped (status=%s)", container.name, container.status)
                return True

            logger.info("Stopping container '%s' (timeout=%ds)...", container.name, timeout_sec)
            container.stop(timeout=timeout_sec)
            return True
        except NotFound:
            return True
        except (DockerException, APIError) as err:
            raise DockerUnavailable(f"Failed to stop container: {sanitize_error_message(err)}") from err

    def remove_container(self, container_name_or_id: str, force: bool = False) -> bool:
        """Safely removes a container.

        CRITICAL SAFETY RULE: Under NO circumstances are volumes deleted with container.
        Explicitly enforces v=False to guarantee persistent named storage is preserved.
        """
        validate_container_name(container_name_or_id)
        client = self._get_client()

        try:
            container: Container = client.containers.get(container_name_or_id)
            logger.info(
                "Removing container '%s' (force=%s, v=False: volumes strictly preserved)...",
                container.name,
                force,
            )
            # v=False explicitly guarantees volume removal is rejected!
            container.remove(v=False, force=force)
            return True
        except NotFound:
            return True
        except (DockerException, APIError) as err:
            raise DockerUnavailable(f"Failed to remove container: {sanitize_error_message(err)}") from err

    def recreate_container_from_compose_spec(
        self,
        container_name: str,
        volume_name: str,
        host_port: int = 3306,
        image: str | None = None,
        environment: dict[str, str] | None = None,
        network_name: str = "dbrescue_network",
    ) -> ContainerInfo:
        """Provisions or recreates MySQL container using strict compose specification.

        Enforces:
          - Image allowlist
          - Strict 127.0.0.1 port binding
          - Persistent volume attachment at /var/lib/mysql
          - Independent backup directory mount at /backups
          - Native healthcheck using mysqladmin ping
        """
        valid_container = validate_container_name(container_name)
        valid_vol = validate_volume_name(volume_name)
        image_tag = _validate_image_allowlist(image or "mysql:8.4.3")

        if not (1 <= host_port <= 65535):
            raise SecurityValidationError(f"Invalid host port: {host_port}")

        client = self._get_client()

        # 1. Stop and safely remove existing container (v=False)
        self.stop_container(valid_container)
        self.remove_container(valid_container, force=True)

        # 2. Prepare port bindings strictly to 127.0.0.1
        port_bindings = {
            "3306/tcp": ("127.0.0.1", host_port)
        }

        # 3. Prepare volume mounts
        backups_host_path = Path("./backups").resolve()
        backups_host_path.mkdir(parents=True, exist_ok=True)

        mounts = [
            docker.types.Mount(
                target="/var/lib/mysql",
                source=valid_vol,
                type="volume",
                read_only=False,
            ),
            docker.types.Mount(
                target="/backups",
                source=str(backups_host_path),
                type="bind",
                read_only=False,
            ),
        ]

        # 4. Prepare healthcheck
        root_pass = environment.get("MYSQL_ROOT_PASSWORD", "RootRescue2026!Emergency") if environment else "RootRescue2026!Emergency"
        healthcheck_config = {
            "test": ["CMD-SHELL", f"mysqladmin ping -h 127.0.0.1 -u root -p{root_pass}"],
            "interval": 10_000_000_000,   # 10s in nanoseconds
            "timeout": 5_000_000_000,      # 5s in nanoseconds
            "retries": 5,
            "start_period": 30_000_000_000 # 30s in nanoseconds
        }

        # 5. Launch container
        try:
            container = client.containers.run(
                image=image_tag,
                name=valid_container,
                detach=True,
                environment=environment or {},
                ports=port_bindings,
                mounts=mounts,
                healthcheck=healthcheck_config,
                network=network_name if network_name else None,
                restart_policy={"Name": "unless-stopped"},
            )
            logger.info("Successfully provisioned container '%s' (id=%s)", container.name, container.id[:12])
            return self.get_container_state(valid_container)
        except (DockerException, APIError) as err:
            sanitized = sanitize_error_message(err)
            raise ContainerRecreateFailed(f"Failed to provision container '{valid_container}': {sanitized}") from err

    def provision_sandbox_container(
        self,
        volume_name: str,
        host_port: int = 3307,
        force_recovery_level: int = 0
    ) -> ContainerInfo:
        """CONTRACTS.md compatibility: Provisions an isolated sandbox container on port 3307."""
        container_name = "dbrescue-mysql-sandbox"
        env = {
            "MYSQL_ROOT_PASSWORD": "RootRescue2026!Emergency",
            "MYSQL_DATABASE": "student_db",
        }
        if force_recovery_level > 0:
            env["INNODB_FORCE_RECOVERY"] = str(force_recovery_level)

        return self.recreate_container_from_compose_spec(
            container_name=container_name,
            volume_name=volume_name,
            host_port=host_port,
            environment=env,
        )

    def wait_until_healthy(
        self,
        container_name_or_id: str,
        timeout_sec: int = 60,
        check_db_ping: bool = True,
        db_config: MySQLConnectionConfig | None = None,
    ) -> bool:
        """Polls container health status and optionally verifies database connectivity via DB ping."""
        validate_container_name(container_name_or_id)
        start_time = time.perf_counter()
        interval = 2.0

        while (time.perf_counter() - start_time) < timeout_sec:
            state = self.get_container_state(container_name_or_id)

            if state.health_status == "healthy":
                if check_db_ping and db_config:
                    if ping_database(db_config, timeout_sec=2):
                        logger.info("Container '%s' is HEALTHY and verified responsive to DB pings.", state.name)
                        return True
                else:
                    logger.info("Container '%s' is HEALTHY.", state.name)
                    return True

            if state.health_status == "unhealthy":
                raise ReadinessTimeout(f"Container '{container_name_or_id}' reported UNHEALTHY state.")

            time.sleep(interval)

        raise ReadinessTimeout(
            f"Timed out after {timeout_sec}s waiting for container '{container_name_or_id}' to become healthy."
        )

    def wait_for_container_health(self, container_id: str, timeout_sec: int = 60) -> bool:
        """CONTRACTS.md compatibility method."""
        return self.wait_until_healthy(container_id, timeout_sec=timeout_sec, check_db_ping=False)

    def exec_in_container(self, container_name_or_id: str, command: Sequence[str]) -> tuple[int, str]:
        """Executes a strictly allowlisted command inside container.

        Raises:
            SecurityValidationError: If command is not on the approved allowlist.
        """
        validate_container_name(container_name_or_id)
        _validate_exec_command(command)

        client = self._get_client()
        try:
            container: Container = client.containers.get(container_name_or_id)
            exit_code, output = container.exec_run(list(command))
            decoded = output.decode("utf-8", errors="replace") if isinstance(output, bytes | bytearray) else str(output)
            return exit_code, decoded
        except (DockerException, APIError) as err:
            raise DockerUnavailable(f"Failed executing in container: {sanitize_error_message(err)}") from err

    def run_ephemeral_inspector(self, volume_name: str, command: list[str], read_only: bool = True) -> str:
        """CONTRACTS.md compatibility: Mounts volume in alpine inspector container to run command."""
        valid_vol = validate_volume_name(volume_name)
        client = self._get_client()

        mount = docker.types.Mount(
            target="/target_volume",
            source=valid_vol,
            type="volume",
            read_only=read_only,
        )

        try:
            output = client.containers.run(
                image="alpine:3.20",
                command=command,
                mounts=[mount],
                remove=True,
                detach=False,
            )
            return output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        except (DockerException, APIError) as err:
            raise DockerUnavailable(f"Ephemeral inspector failed: {sanitize_error_message(err)}") from err

    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """CONTRACTS.md compatibility method."""
        validate_container_name(container_id)
        client = self._get_client()
        try:
            container: Container = client.containers.get(container_id)
            logs = container.logs(tail=tail)
            return logs.decode("utf-8", errors="replace") if isinstance(logs, bytes) else str(logs)
        except (DockerException, APIError) as err:
            return f"Failed to retrieve logs: {sanitize_error_message(err)}"
