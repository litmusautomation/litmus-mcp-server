import asyncio
from functools import partial


async def run_sync(func, *args, **kwargs):
    """Run a synchronous function in a thread to avoid blocking the event loop."""
    if kwargs:
        func = partial(func, *args, **kwargs)
        return await asyncio.to_thread(func)
    return await asyncio.to_thread(func, *args)
