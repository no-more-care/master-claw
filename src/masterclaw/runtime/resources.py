from __future__ import annotations

import asyncio
from typing import Protocol


class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


class RuntimeResources:
    """Own a group of async resources, closing each once in reverse acquisition order."""

    def __init__(self, *resources: AsyncCloseable) -> None:
        self._resources: list[AsyncCloseable] = []
        self._close_task: asyncio.Task[None] | None = None
        for resource in resources:
            self.add(resource)

    def add(self, resource: AsyncCloseable) -> None:
        if self._close_task is not None:
            raise RuntimeError("runtime resources are closing")
        if not any(existing is resource for existing in self._resources):
            self._resources.append(resource)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_all())
        # Cancelling one waiter must not abandon cleanup of the remaining resources.
        await asyncio.shield(self._close_task)

    async def _close_all(self) -> None:
        errors: list[Exception] = []
        for resource in reversed(self._resources):
            try:
                await resource.aclose()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("runtime resource cleanup failed", errors)
