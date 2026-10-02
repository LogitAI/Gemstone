"""
The lifetime of a request's model lease (SPEC S1.14).

Every `registry.acquire` must be followed by exactly one `registry.release`, or a model switch
waits forever. Two places make that easy to get wrong:

- `acquire` runs on a worker thread. If the request is cancelled while it waits (for a model
  switch, say), the thread is abandoned and the lease it is granted afterwards would be lost.
  `acquire` here releases it.
- A streaming route leases the model before it returns its response. The body's own `finally` only
  runs if the body is iterated, which never happens when the client is gone before the response
  starts, or when the response is dropped unsent (#93). `LeasedStreamingResponse` runs its
  cleanup when the response ends however it ends, and when it is garbage-collected unsent.
"""
from typing import Callable
import threading
import weakref

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse


def once(fn: Callable[[], None]) -> Callable[[], None]:
    """ `fn` runs on the first call only, from whichever thread or path gets there first. """
    lock, done = threading.Lock(), []

    def wrapper():
        with lock:
            if done:
                return
            done.append(True)
        fn()
    return wrapper


async def acquire(registry, *args):
    """
    `registry.acquire(*args)` off the event loop. If the request is cancelled while it waits, the
    lease is released instead of lost, whether the worker thread is still waiting (asyncio's own
    cancellation abandons it) or has already been granted the lease.
    """
    lock = threading.Lock()
    state = dict(lease=None, abandoned=False)

    def work():
        lease = registry.acquire(*args)
        with lock:
            abandoned = state["abandoned"]
            if not abandoned:
                state["lease"] = lease
        if abandoned:
            registry.release(lease)
        return lease

    try:
        return await run_in_threadpool(work)
    except BaseException:
        with lock:
            state["abandoned"] = True
            lease, state["lease"] = state["lease"], None
        if lease is not None:
            registry.release(lease)
        raise


class LeasedStreamingResponse(StreamingResponse):
    """
    A `StreamingResponse` that runs `cleanup` (stop the generation, release the lease) exactly
    once: when the response has been sent, when it ends without its body being read (the client
    went away before it started), or when it is garbage-collected without ever being sent. The
    body may call the same `cleanup` from its own `finally`; only the first call runs it.
    """

    def __init__(self, content, cleanup: Callable[[], None], **kwargs):
        super().__init__(content, **kwargs)
        # The finalizer holds `cleanup`, never the response, so the response can still be collected.
        self._cleanup = weakref.finalize(self, once(cleanup))
        self._cleanup.atexit = False

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self._cleanup)
