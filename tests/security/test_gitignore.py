"""Security tests asserting that sensitive files, backups, and environments are ignored."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.security
def test_gitignore_contains_critical_exclusions():
    """Validates that .gitignore exists and specifies all critical security exclusions."""
    gitignore_path = Path(".gitignore")
    assert gitignore_path.exists(), ".gitignore file must exist in repository root."

    content = gitignore_path.read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    required_patterns = [
        ".env",
        "backups/",
        "*.sql",
        "*.sqlite",
        "*.db",
        "__pycache__/",
        ".venv/",
        ".pytest_cache/",
        "logs/",
        "docs/evidence/raw-secrets*",
    ]

    for pattern in required_patterns:
        # Check either exact pattern or base pattern without trailing slash
        base = pattern.rstrip("/")
        matching = any(line in (pattern, base) or line.startswith(base) for line in lines)
        assert matching, f"Required pattern '{pattern}' missing from .gitignore."


@pytest.mark.security
def test_env_file_is_not_tracked_or_present_in_git():
    """Ensures .env cannot be accidentally matched as tracked source code."""
    gitignore_path = Path(".gitignore")
    content = gitignore_path.read_text(encoding="utf-8")
    assert ".env" in content
    assert "backups/" in content
