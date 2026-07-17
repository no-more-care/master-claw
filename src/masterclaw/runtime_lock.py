from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class InstanceAlreadyRunning(RuntimeError):
    pass


def _lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def single_instance(database_path: str | Path) -> Iterator[None]:
    """Hold an OS lock for one daemon per database until the process exits."""
    path = Path(f"{database_path}.serve.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        try:
            _lock(handle)
        except OSError as error:
            raise InstanceAlreadyRunning(
                f"another MasterClaw daemon already uses {database_path}"
            ) from error
        try:
            yield
        finally:
            _unlock(handle)
