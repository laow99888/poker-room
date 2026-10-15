"""公开推演的并发准入及计算截止时间；不减少采样次数，不返回半成品结果。"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
import threading
import time

from fastapi import HTTPException

_slots = threading.BoundedSemaphore(max(1, min(8, int(os.environ.get("POKER_COMPUTE_SLOTS", "2")))))
_deadline = ContextVar("poker_compute_deadline", default=None)
SECONDS = 90


def checkpoint():
    end = _deadline.get()
    if end is not None and time.monotonic() >= end:
        raise HTTPException(503, "本次推演超过计算时限，请重试", headers={"Retry-After": "2"})


@contextmanager
def slot():
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "推演服务繁忙，请稍后重试", headers={"Retry-After": "2"})
    token = _deadline.set(time.monotonic() + SECONDS)
    try:
        yield
        checkpoint()
    finally:
        _deadline.reset(token)
        _slots.release()


def limited(function):
    @wraps(function)
    def run(*args, **kwargs):
        with slot():
            return function(*args, **kwargs)
    return run
