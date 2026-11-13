"""In-process reservation guard for an explicitly granted multi-step Flow run."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from aim_ci.owned_paths import Refused

_GUARD = ContextVar('flow_generation_authority', default=None)


@contextmanager
def authority(callback):
    token = _GUARD.set(callback)
    try:
        yield
    finally:
        _GUARD.reset(token)


def check(campaign, data, state, amount):
    callback = _GUARD.get()
    if callback is not None:
        callback(campaign, data, Path(state), amount)
