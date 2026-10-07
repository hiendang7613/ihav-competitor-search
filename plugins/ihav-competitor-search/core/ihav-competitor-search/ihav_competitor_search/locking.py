"""One local writer for answer, launch and cell-evidence state."""
from contextlib import contextmanager


@contextmanager
def ask_lock(directory):
    lock = directory / "ask.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("ask.lock exists; reconcile the prior writer before removing it") from None
    try:
        yield
    finally:
        lock.rmdir()
