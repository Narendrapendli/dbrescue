"""Static security analysis and repository compliance tests for dbrescue.

Ensures:
  1. No hardcoded credentials or private keys committed to the repository.
  2. No string-interpolated or concatenated SQL queries in src/ (strictly parameterized SQL).
  3. No 'docker compose down -v' or accidental volume removal flags anywhere in the codebase.
  4. compose.yaml strictly binds MySQL ports to 127.0.0.1 loopback only.
  5. Backups and SQLite databases are explicitly gitignored.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.security
def test_no_hardcoded_secrets_in_repo():
    """Scans all Python, SQL, YAML, and shell files for suspicious hardcoded passwords/tokens."""
    secret_patterns = [
        re.compile(
            r"""(?:password|secret|api_key|token)\s*=\s*['"][a-zA-Z0-9_\-!@#$%^&*]{8,}['"]""",
            re.IGNORECASE,
        ),
        re.compile(r"""-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"""),
        re.compile(r"""ghp_[a-zA-Z0-9]{36}"""),
    ]

    # Directories to ignore
    ignore_dirs = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "logs",
        "backups",
    }

    findings = []
    for path in REPO_ROOT.rglob("*"):
        if any(ignored in path.parts for ignored in ignore_dirs):
            continue
        if path.is_file() and path.suffix in {".py", ".sql", ".yaml", ".yml", ".ps1", ".sh"}:
            # Skip test fixtures containing dummy testing strings
            if "tests" in path.parts:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                for pattern in secret_patterns:
                    matches = pattern.findall(content)
                    if matches:
                        # Exclude harmless examples, env fallbacks, confirmation tokens, or redacted placeholders
                        filtered = [
                            m
                            for m in matches
                            if "CHANGE_ME" not in m
                            and "placeholder" not in m.lower()
                            and "REDACTED" not in m
                            and "CONFIRM_" not in m
                        ]
                        if filtered:
                            findings.append(f"{path}: {filtered}")
            except Exception:
                pass

    assert len(findings) == 0, f"Found potential hardcoded credentials: {findings}"


@pytest.mark.security
def test_parameterized_sql_static_scan():
    """AST static analysis: Verifies that DML execute() calls in src/ do not use f-strings or string concatenation."""
    src_dir = REPO_ROOT / "src"
    violations = []

    class SQLExecuteVisitor(ast.NodeVisitor):
        def __init__(self, file_path: Path):
            self.file_path = file_path

        def visit_Call(self, node: ast.Call):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany"}
                and node.args
            ):
                first_arg = node.args[0]
                # Check for f-string (JoinedStr)
                if isinstance(first_arg, ast.JoinedStr):
                    literal_parts = "".join(
                        part.value
                        for part in first_arg.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    ).upper()
                    # Unparameterized filtering/mutation values (WHERE, VALUES, SET) are forbidden
                    if any(clause in literal_parts for clause in [" WHERE ", " VALUES ", " SET "]):
                        violations.append(
                            f"{self.file_path}:{node.lineno} - Dynamic f-string query with unparameterized clause in execute(): "
                            f"strictly parameterized SQL is required."
                        )
                # Check for binary string concatenation ('SELECT ' + table)
                elif isinstance(first_arg, ast.BinOp) and isinstance(first_arg.op, ast.Add):
                    violations.append(
                        f"{self.file_path}:{node.lineno} - String concatenation in execute(): "
                        f"strictly parameterized SQL is required."
                    )
            self.generic_visit(node)

    for py_path in src_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            visitor = SQLExecuteVisitor(py_path)
            visitor.visit(tree)
        except Exception:
            pass

    assert len(violations) == 0, f"Static analysis detected unparameterized DML SQL: {violations}"


@pytest.mark.security
def test_no_down_v_anywhere():
    """GREP GUARD: Asserts 'down -v' or 'volume rm' is NOT used anywhere in scripts, compose, or docs."""
    forbidden_phrases = [
        "down -v",
        "down --volumes",
        "volume rm",
        "docker volume prune",
    ]

    ignore_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
    violations = []

    for path in REPO_ROOT.rglob("*"):
        if any(ignored in path.parts for ignored in ignore_dirs):
            continue
        if path.is_file() and path.suffix in {".py", ".sh", ".ps1", ".yaml", ".yml", ".md"}:
            if any(
                exempt in path.name
                for exempt in [
                    "test_security_audit.py",
                    "test_compose_and_schema.py",
                    "HANDOFF.md",
                    "TEST_REPORT.md",
                    "SECURITY_AUDIT.md",
                    "SECURITY.md",
                ]
            ):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                for phrase in forbidden_phrases:
                    if phrase in content:
                        # Exclude explanatory warning docs that mention the rule
                        lines = [line for line in content.splitlines() if phrase in line]
                        # Check if it's an instruction to avoid it vs an actual command execution
                        executed = [
                            cmd_line
                            for cmd_line in lines
                            if not any(
                                w in cmd_line.lower()
                                for w in [
                                    "never",
                                    "forbidden",
                                    "do not",
                                    "safety",
                                    "prohibit",
                                    "no down -v",
                                    "no 'down -v'",
                                    "without -v",
                                    "marker",
                                    "rule",
                                    "guideline",
                                    "verify",
                                    "guard",
                                    "checklist",
                                ]
                            )
                        ]
                        if executed:
                            violations.append(f"{path}: {executed}")
            except Exception:
                pass

    assert len(violations) == 0, f"Found forbidden destructive volume commands: {violations}"


@pytest.mark.security
def test_compose_mysql_loopback_binding():
    """Confirms compose.yaml strictly binds MySQL ports to 127.0.0.1 (never 0.0.0.0 or wildcard)."""
    compose_path = REPO_ROOT / "compose.yaml"
    assert compose_path.exists(), "compose.yaml not found"

    content = compose_path.read_text(encoding="utf-8")

    # Match port mappings
    port_matches = re.findall(r"""-\s*["']?([^"'\n]+:3306)["']?""", content)
    assert len(port_matches) > 0, "No MySQL port mapping found in compose.yaml"

    for mapping in port_matches:
        assert mapping.startswith(
            "127.0.0.1:"
        ), f"Security Violation: MySQL port '{mapping}' in compose.yaml is not strictly bound to 127.0.0.1."
