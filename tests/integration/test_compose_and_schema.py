"""Integration tests validating Docker Compose configuration, schemas, users, and safety rules."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.mark.integration
def test_compose_yaml_specifications():
    """Validates that compose.yaml adheres strictly to security and infrastructure contracts."""
    compose_path = Path("compose.yaml")
    assert compose_path.exists(), "compose.yaml must exist."

    content = compose_path.read_text(encoding="utf-8")

    # 1. Pinned MySQL image tag (never :latest)
    assert "image: mysql:8.4.3" in content, "Must use verified pinned tag mysql:8.4.3"
    assert ":latest" not in content, "Floating tag :latest is strictly forbidden"

    # 2. Strict loopback port binding (127.0.0.1)
    assert (
        "127.0.0.1:${MYSQL_PRIMARY_PORT:-3306}:3306" in content
    ), "MySQL port must bind strictly to 127.0.0.1"
    assert "0.0.0.0:3306" not in content, "Binding to 0.0.0.0 is forbidden for MySQL"

    # 3. Named persistent volume
    assert (
        "mysql_data:/var/lib/mysql" in content
    ), "Must mount named volume mysql_data to /var/lib/mysql"
    assert "mysql_data:" in content, "mysql_data must be declared in top-level volumes"

    # 4. Healthcheck with mysqladmin ping
    assert "mysqladmin ping" in content, "Must define healthcheck using mysqladmin ping"
    assert "start_period: 30s" in content, "Healthcheck must configure start_period"

    # 5. Backup directory bind-mounted outside data volume
    assert "./backups:/backups:rw" in content, "Backups directory must be mounted independently"

    # 6. Dedicated network
    assert "dbrescue_net" in content, "Must use dedicated dbrescue_net network"

    # 7. Resource limits
    assert (
        "limits:" in content and "memory: 2048M" in content
    ), "Must enforce resource limits on MySQL"


@pytest.mark.integration
def test_schema_sql_definitions():
    """Validates that db/init/01_schema.sql defines student_db, dbrescue_test, and tables."""
    schema_path = Path("db/init/01_schema.sql")
    assert schema_path.exists(), "db/init/01_schema.sql must exist."

    content = schema_path.read_text(encoding="utf-8")

    assert "CREATE DATABASE IF NOT EXISTS student_db" in content
    assert "CREATE DATABASE IF NOT EXISTS dbrescue_test" in content
    assert "CREATE TABLE IF NOT EXISTS students" in content
    assert "CREATE TABLE IF NOT EXISTS courses" in content
    assert "CREATE TABLE IF NOT EXISTS enrollments" in content
    assert "CREATE TABLE IF NOT EXISTS test_records" in content
    assert "CONSTRAINT fk_enrollments_student" in content
    assert "CONSTRAINT fk_enrollments_course" in content


@pytest.mark.integration
def test_users_sql_least_privilege():
    """Validates that db/init/02_users.sql creates least-privilege users without root use."""
    users_path = Path("db/init/02_users.sql")
    assert users_path.exists(), "db/init/02_users.sql must exist."

    content = users_path.read_text(encoding="utf-8")

    assert "CREATE USER IF NOT EXISTS 'rescue_app'@'%'" in content
    assert "CREATE USER IF NOT EXISTS 'rescue_backup'@'%'" in content
    assert "CREATE USER IF NOT EXISTS 'rescue_restore'@'%'" in content

    # Check privilege restrictions
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in content
    assert "GRANT SELECT, LOCK TABLES, SHOW VIEW, TRIGGER, EVENT" in content
    assert "GRANT ALL PRIVILEGES ON dbrescue_test.* TO 'rescue_restore'@'%'" in content


@pytest.mark.integration
def test_seed_students_count_and_idempotence():
    """Validates that seed_students.sql contains exactly 100 students and uses idempotent syntax."""
    seed_path = Path("db/seed/seed_students.sql")
    assert seed_path.exists(), "db/seed/seed_students.sql must exist."

    content = seed_path.read_text(encoding="utf-8")

    # Idempotence keyword check
    assert "ON DUPLICATE KEY UPDATE" in content, "Seed statements must be idempotent"

    # Count synthetic students (pattern matches (1, '10000000-0000-4000-8000-...', ...)
    student_records = re.findall(r"\(\s*\d+\s*,\s*'10000000-[^']+'", content)
    assert (
        len(student_records) == 100
    ), f"Expected 100 synthetic students, found {len(student_records)}"

    # Count courses
    course_records = re.findall(r"\(\s*\d+\s*,\s*'[^']+'\s*,\s*'[^']+'\s*,\s*'[^']+'", content)
    assert len(course_records) >= 10, f"Expected at least 10 courses, found {len(course_records)}"

    # Check for test record insertion
    assert "INSERT INTO test_records" in content
    assert "STUDENT_SEED_COMPLETE" in content


@pytest.mark.security
def test_scripts_no_volume_removal_guardrail():
    """Enforces safety guardrail: NO script may contain 'down -v' or volume removal commands."""
    scripts_dir = Path("scripts")
    assert scripts_dir.exists()

    forbidden_patterns = ["down -v", "volume rm", "volumes rm", "docker volume prune"]

    for script_file in scripts_dir.glob("*"):
        if script_file.is_file():
            text = script_file.read_text(encoding="utf-8").lower()
            for pattern in forbidden_patterns:
                assert pattern not in text, (
                    f"CRITICAL SAFETY VIOLATION: '{pattern}' found in {script_file}. "
                    f"Scripts are strictly forbidden from destroying persistent storage."
                )
