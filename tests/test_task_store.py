#!/usr/bin/env python3
"""Unit tests for task_store.py."""
from pathlib import Path
import sys
import pytest

PLAN_DIR = Path(__file__).resolve().parent.parent
if str(PLAN_DIR) not in sys.path:
    sys.path.insert(0, str(PLAN_DIR))

from school_plan import task_store


def test_atomic_save_preserves_original_on_halfway_error(tmp_path):
    tasks_file = tmp_path / "tasks.json"
    initial = [{"id": "1", "title": "Original — tâche é"}]
    task_store.save(initial, path=tasks_file)

    def broken_hook(fh):
        fh.write('[{"id": "broken"')
        fh.flush()
        raise RuntimeError("Simulated mid-write crash")

    with pytest.raises(RuntimeError, match="Simulated mid-write crash"):
        task_store.save([{"id": "2", "title": "New"}], path=tasks_file, _writer_hook=broken_hook)

    assert task_store.load(path=tasks_file) == initial
    # Ensure no leftover .tmp files
    assert list(tmp_path.glob("*.tmp")) == []


def test_non_blocking_lock_raises_lock_busy(tmp_path):
    lock_file = tmp_path / "test.lock"
    with task_store.locked(blocking=False, timeout=0, lock_path=lock_file):
        with pytest.raises(task_store.LockBusy):
            with task_store.locked(blocking=False, timeout=0, lock_path=lock_file):
                pass


def test_run_lock_and_write_lock_are_independent(tmp_path):
    run_lock = tmp_path / "school-plan.run.lock"
    write_lock = tmp_path / "school-plan.lock"
    tasks_file = tmp_path / "tasks.json"

    with task_store.run_locked(blocking=False, lock_path=run_lock):
        # Short write lock succeeds while run lock is held
        updated = task_store.update(
            lambda items: items + [{"id": "u1", "title": "Épreuve — Révision C#"}],
            timeout=0.5,
            path=tasks_file,
            lock_path=write_lock,
        )
        assert len(updated) == 1
        assert updated[0]["title"] == "Épreuve — Révision C#"


def test_update_round_trips_unicode_and_creates_prev_and_backup(tmp_path):
    tasks_file = tmp_path / "tasks.json"
    lock_file = tmp_path / "school-plan.lock"

    task_store.save([{"id": "a", "title": "Première tâche — éàè"}], path=tasks_file)
    task_store.update(
        lambda items: items + [{"id": "b", "title": "Deuxième — œuvre"}],
        path=tasks_file,
        lock_path=lock_file,
    )

    loaded = task_store.load(path=tasks_file)
    assert [x["id"] for x in loaded] == ["a", "b"]
    assert loaded[1]["title"] == "Deuxième — œuvre"
    assert (tmp_path / "tasks.json.prev").exists()

    bak = task_store.backup("pre-v2", path=tasks_file)
    assert bak is not None and bak.exists()
