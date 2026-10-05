"""Serialize model requests across threads and cooperating local processes."""

from __future__ import annotations

import errno
import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from interview_intelligence.providers.runtime import current_limits


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


class ModelCallGate:
    """Hold a file lock during a request, with a quiet interval after completion.

    API and worker processes must use the same mounted lock file. The OS releases
    its lock if a process exits. The file stores only the last completion time.
    """

    def __init__(self, lock_path: Path | str = Path("data/model-call.lock"), *,
                 minimum_interval_seconds: float = 2.0):
        if not math.isfinite(minimum_interval_seconds) or minimum_interval_seconds < 0:
            raise ValueError("minimum model call interval must be finite and non-negative")
        self.lock_path = Path(lock_path).resolve()
        self.minimum_interval_seconds = minimum_interval_seconds
        with _REGISTRY_LOCK:
            self._thread_lock = _THREAD_LOCKS.setdefault(str(self.lock_path), threading.Lock())

    @contextmanager
    def call(self):
        limits = current_limits.get()
        if limits:
            limits.attempt()
        started = time.perf_counter()
        timings = {"queue_ms": 0, "interval_ms": 0}
        def check():
            if limits:
                limits.check()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while not self._thread_lock.acquire(timeout=0.05):
            check()
        try:
            check()
            for attempt in range(5):
                try:
                    descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.2 * 2 ** attempt)
            with os.fdopen(descriptor, "r+b") as lock_file:
                self._acquire(lock_file, check)
                try:
                    # The first byte must also be initialized under the lock.
                    # Windows permits locking beyond EOF, but another process's
                    # lock can reject a pre-acquisition write to an empty file.
                    if os.fstat(lock_file.fileno()).st_size == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    timings["queue_ms"] = int((time.perf_counter() - started) * 1000)
                    lock_file.seek(1)
                    try:
                        previous_completion = float(lock_file.read(64).decode("ascii"))
                    except (ValueError, UnicodeDecodeError):
                        previous_completion = 0.0
                    # A clock correction must not produce an unbounded pause.
                    remaining = min(self.minimum_interval_seconds,
                                    previous_completion + self.minimum_interval_seconds - time.time())
                    if remaining > 0:
                        interval_start = time.perf_counter()
                        until = time.monotonic() + remaining
                        while time.monotonic() < until:
                            check()
                            time.sleep(min(0.05, max(0, until - time.monotonic())))
                        timings["interval_ms"] = int((time.perf_counter() - interval_start) * 1000)
                    check()
                    try:
                        yield timings
                    finally:
                        lock_file.seek(1)
                        lock_file.write(f"{time.time():.9f}".encode("ascii"))
                        lock_file.truncate()
                        lock_file.flush()
                finally:
                    self._release(lock_file)
        finally:
            self._thread_lock.release()

    @staticmethod
    def _acquire(lock_file, check=lambda: None):
        if os.name == "nt":
            import msvcrt
            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    check()
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    check()
                    time.sleep(0.05)

    @staticmethod
    def _release(lock_file):
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
