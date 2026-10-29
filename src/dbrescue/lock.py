"""Cross-platform atomic file lock with stale-lock detection and timeout.

Guarantees mutual exclusion for destructive recovery operations across both Windows and Unix
environments, preventing concurrent state machine collisions or data corruption.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbrescue.config import RescueException

logger = logging.getLogger("dbrescue.lock")


class LockError(RescueException):
    """Base exception for file locking failures."""

    pass


class LockAcquisitionTimeout(LockError):
    """Raised when acquiring the file lock exceeds the configured timeout."""

    pass


class LockContentionError(LockError):
    """Raised when an operation is blocked by another active recovery run."""

    pass


@dataclass(frozen=True)
class LockMetadata:
    owner_pid: int
    acquired_at: float
    run_id: str
    hostname: str


class FileLock:
    """Cross-platform advisory file lock with timeout and stale-lock recovery."""

    def __init__(
        self,
        lock_file_path: Path | str,
        timeout_sec: float = 10.0,
        stale_timeout_sec: float = 300.0,
        run_id: str = "unknown",
    ) -> None:
        self.lock_path = Path(lock_file_path).resolve()
        self.timeout_sec = timeout_sec
        self.stale_timeout_sec = stale_timeout_sec
        self.run_id = run_id
        self._is_locked = False
        self._file_handle: int | None = None

    def _is_pid_alive(self, pid: int) -> bool:
        """Checks if process with given PID is still active on the host OS."""
        if pid <= 0:
            return False
        try:
            if sys.platform == "win32":
                import ctypes

                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                SYNCHRONIZE = 0x00100000
                process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if process:
                    kernel32.CloseHandle(process)
                    return True
                return False
            else:
                os.kill(pid, 0)
                return True
        except (OSError, ProcessLookupError, PermissionError):
            return False

    def _check_and_break_stale_lock(self) -> bool:
        """Detects if existing lock file is abandoned (dead process or timed out)."""
        if not self.lock_path.exists():
            return False

        try:
            content = self.lock_path.read_text(encoding="utf-8")
            data = json.loads(content)
            owner_pid = data.get("owner_pid", -1)
            acquired_at = data.get("acquired_at", 0.0)

            elapsed = time.time() - acquired_at

            # Condition 1: Lock older than stale timeout
            if elapsed > self.stale_timeout_sec:
                logger.warning(
                    "Stale lock detected on '%s' (age: %.1fs > %.1fs). Breaking stale lock.",
                    self.lock_path,
                    elapsed,
                    self.stale_timeout_sec,
                )
                self.lock_path.unlink(missing_ok=True)
                return True

            # Condition 2: Process that created the lock is no longer alive
            if not self._is_pid_alive(owner_pid):
                logger.warning(
                    "Dead process PID %d held lock on '%s'. Breaking stale lock.",
                    owner_pid,
                    self.lock_path,
                )
                self.lock_path.unlink(missing_ok=True)
                return True

        except Exception as err:
            logger.debug("Error inspecting lockfile for staleness: %s", err)
            # Corrupted lockfile - unlink safely
            self.lock_path.unlink(missing_ok=True)
            return True

        return False

    def acquire(self) -> bool:
        """Blocks until the lock is acquired or timeout expires."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.perf_counter()
        poll_interval = 0.2

        while (time.perf_counter() - start_time) < self.timeout_sec:
            # Check for stale lock
            self._check_and_break_stale_lock()

            try:
                # Use atomic OS creation flag: O_CREAT | O_EXCL
                flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
                fd = os.open(str(self.lock_path), flags, 0o644)
                self._file_handle = fd

                meta = {
                    "owner_pid": os.getpid(),
                    "acquired_at": time.time(),
                    "run_id": self.run_id,
                    "hostname": os.getenv("COMPUTERNAME", os.getenv("HOSTNAME", "localhost")),
                }
                meta_bytes = json.dumps(meta).encode("utf-8")
                os.write(fd, meta_bytes)
                self._is_locked = True
                logger.debug("Acquired lock on '%s' for run '%s'", self.lock_path, self.run_id)
                return True
            except FileExistsError:
                time.sleep(poll_interval)
            except OSError:
                time.sleep(poll_interval)

        raise LockAcquisitionTimeout(
            f"Timed out after {self.timeout_sec}s attempting to acquire lock on '{self.lock_path}'. "
            f"Another recovery operation is currently running."
        )

    def release(self) -> None:
        """Releases the lock and deletes the lock file."""
        if self._file_handle is not None:
            with contextlib.suppress(OSError):
                os.close(self._file_handle)
            self._file_handle = None

        if self.lock_path.exists():
            with contextlib.suppress(OSError):
                self.lock_path.unlink(missing_ok=True)
                logger.debug("Released lock on '%s'", self.lock_path)

        self._is_locked = False

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


@contextmanager
def acquire_recovery_lock(
    lock_path: Path | str, timeout_sec: float = 10.0, run_id: str = "unknown"
) -> Generator[FileLock, None, None]:
    """Helper context manager acquiring recovery file lock."""
    lock = FileLock(lock_path, timeout_sec=timeout_sec, run_id=run_id)
    with lock:
        yield lock
