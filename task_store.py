#!/usr/bin/env python3
"""
Atomic, flock-protected persistence and run-lock helpers for tasks.json.
"""

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable
import fcntl
import json
import os
import shutil
import time

import config


class LockBusy(RuntimeError):
    """Raised when a file lock cannot be acquired within the requested timeout."""


@contextmanager
def locked(
    blocking: bool = True,
    timeout: float = 2.0,
    lock_path: Path | str | None = None,
):
    """Acquires an exclusive flock on lock_path (defaults to config.LOCK_PATH)."""
    target_lock = Path(lock_path) if lock_path is not None else config.LOCK_PATH
    target_lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target_lock), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    deadline = time.monotonic() + (timeout if blocking and timeout > 0 else 0.0)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if not blocking or time.monotonic() >= deadline:
                    raise LockBusy(f"Lock busy: {target_lock}")
                time.sleep(0.03)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


@contextmanager
def run_locked(
    blocking: bool = False,
    timeout: float = 0.0,
    lock_path: Path | str | None = None,
):
    """Acquires the long-running pipeline lock (school-plan.run.lock)."""
    target_lock = Path(lock_path) if lock_path is not None else config.RUN_LOCK_PATH
    with locked(blocking=blocking, timeout=timeout, lock_path=target_lock):
        yield


def load(path: Path | str | None = None) -> list[dict]:
    """Reads tasks list from tasks.json (returns [] if missing)."""
    target = Path(path) if path is not None else config.TASKS_PATH
    if not target.exists():
        return []
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save(
    tasks: list[dict],
    path: Path | str | None = None,
    _writer_hook: Callable[[object], None] | None = None,
) -> None:
    """Atomically writes tasks list to path via temp-file + fsync + os.replace and keeps one .prev."""
    target = Path(path) if path is not None else config.TASKS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.parent / f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp"
    prev_path = target.parent / f"{target.name}.prev"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            if _writer_hook is not None:
                _writer_hook(f)
            json.dump(tasks, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if target.exists():
            shutil.copy2(target, prev_path)
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def update(
    fn: Callable[[list[dict]], list[dict] | None],
    timeout: float = 2.0,
    path: Path | str | None = None,
    lock_path: Path | str | None = None,
) -> list[dict]:
    """Locks, loads tasks, applies fn(tasks), saves atomically, and returns updated tasks."""
    with locked(blocking=timeout > 0, timeout=timeout, lock_path=lock_path):
        current = load(path=path)
        result = fn(current)
        updated = current if result is None else result
        save(updated, path=path)
        return updated


def backup(tag: str = "manual", path: Path | str | None = None) -> Path | None:
    """Creates a timestamped backup tasks.json.bak-<tag>-<ts> if the file exists."""
    target = Path(path) if path is not None else config.TASKS_PATH
    if not target.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_path = target.parent / f"{target.name}.bak-{tag}-{ts}"
    shutil.copy2(target, bak_path)
    return bak_path
