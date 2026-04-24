# -*- coding: utf-8 -*-

"""Request concurrency limiter helpers."""

import asyncio
from typing import Any


class RequestLimiterBusy(Exception):
    """Raised when a request cannot acquire a limiter slot in time."""


async def acquire_request_slot(app_state: Any) -> bool:
    """获取模型请求并发槽位，成功返回 True，超时抛出忙碌异常。"""
    limiter = getattr(app_state, "request_limiter", None)
    if limiter is None:
        return False

    queue_timeout = getattr(app_state, "request_queue_timeout", 5.0)
    try:
        await asyncio.wait_for(limiter.acquire(), timeout=queue_timeout)
    except asyncio.TimeoutError as exc:
        raise RequestLimiterBusy("Server is busy. Please retry later.") from exc

    return True


def release_request_slot(app_state: Any, acquired: bool) -> None:
    """释放已经获取的模型请求并发槽位。"""
    if not acquired:
        return

    limiter = getattr(app_state, "request_limiter", None)
    if limiter is not None:
        limiter.release()
