#!/usr/bin/env python3
"""Shared safeguards for Hancom GUI automation flows."""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def hancom_automation_lock(
    *,
    timeout_seconds: int = 300,
    poll_interval: float = 0.25,
) -> Iterator[None]:
    """Serialize Hancom GUI automation to avoid RPC collisions."""
    if os.name != "nt":
        yield
        return

    import msvcrt

    lock_path = Path(tempfile.gettempdir()) / "hwpx_skill_hancom.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout_seconds
    with open(lock_path, "a+b") as lock_file:
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()

        lock_file.seek(0)
        while True:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out while waiting for Hancom automation lock.")
                time.sleep(poll_interval)
                lock_file.seek(0)

        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
