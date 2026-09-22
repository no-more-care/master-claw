import asyncio

import pytest

from masterclaw.runtime.resources import RuntimeResources


def test_resources_close_once_in_reverse_order_with_concurrent_waiters():
    order = []

    class Resource:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            await asyncio.sleep(0)
            order.append(self.name)

    async def run():
        first, second = Resource("first"), Resource("second")
        resources = RuntimeResources(first, second, first)
        await asyncio.gather(resources.aclose(), resources.aclose())
        await resources.aclose()
        with pytest.raises(RuntimeError, match="closing"):
            resources.add(Resource("late"))

    asyncio.run(run())
    assert order == ["second", "first"]


def test_cleanup_failure_does_not_skip_other_resources_or_repeat_closes():
    closed = []

    class Resource:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(self.name)
            if self.name == "second":
                raise RuntimeError("close failed")

    async def run():
        resources = RuntimeResources(Resource("first"), Resource("second"))
        for _ in range(2):
            with pytest.raises(ExceptionGroup):
                await resources.aclose()

    asyncio.run(run())
    assert closed == ["second", "first"]


def test_cancelled_waiter_does_not_cancel_resource_cleanup():
    closed = []

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Resource:
            async def aclose(self):
                started.set()
                await release.wait()
                closed.append("closed")

        resources = RuntimeResources(Resource())
        task = asyncio.create_task(resources.aclose())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await resources.aclose()

    asyncio.run(run())
    assert closed == ["closed"]
