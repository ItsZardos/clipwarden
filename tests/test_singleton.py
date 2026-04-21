"""Single-instance mutex tests.

``pywin32`` is replaced with a dict-backed fake so the tests stay
hermetic: we do not want a runaway test run to create real kernel
objects under the ``Local\\`` namespace that outlive the pytest
process.
"""

from __future__ import annotations

import pytest

from clipwarden import singleton


class _FakeWin32:
    """Minimal stand-in for the pywin32 API surface ``singleton`` uses.

    Acts as both ``_event`` (``CreateMutex``) and ``_api``
    (``GetLastError``, ``CloseHandle``) so the two module aliases can
    be pointed at the same instance.
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()
        self._closed: list[int] = []
        self._last_error = 0
        self._next_handle = 1000

    def _mint_handle(self) -> int:
        self._next_handle += 1
        return self._next_handle

    def CreateMutex(self, _sec, _initial, name):  # noqa: N802 - pywin32 shape
        handle = self._mint_handle()
        if name in self._taken:
            self._last_error = singleton._ERROR_ALREADY_EXISTS
            return handle
        self._taken.add(name)
        self._last_error = 0
        return handle

    def GetLastError(self) -> int:  # noqa: N802
        return self._last_error

    def CloseHandle(self, handle) -> None:  # noqa: N802
        self._closed.append(handle)


@pytest.fixture
def fake(monkeypatch):
    f = _FakeWin32()
    monkeypatch.setattr(singleton, "_event", f, raising=True)
    monkeypatch.setattr(singleton, "_api", f, raising=True)
    return f


def test_first_acquire_returns_handle(fake):
    h = singleton.acquire("Local\\X")
    assert h is not None
    assert h.handle is not None


def test_second_acquire_same_name_returns_none(fake):
    first = singleton.acquire("Local\\X")
    assert first is not None
    second = singleton.acquire("Local\\X")
    assert second is None


def test_collision_closes_duplicate_handle(fake):
    singleton.acquire("Local\\X")
    before = len(fake._closed)
    singleton.acquire("Local\\X")
    # The failed second attempt must close its handle to avoid
    # leaking a kernel object on the ERROR_ALREADY_EXISTS path.
    assert len(fake._closed) == before + 1


def test_release_is_idempotent(fake):
    h = singleton.acquire("Local\\X")
    assert h is not None
    h.release()
    h.release()
    assert fake._closed.count(h.handle) == 1


def test_context_manager_releases(fake):
    ctx = singleton.acquire("Local\\X")
    assert ctx is not None
    with ctx as h:
        assert h.handle not in fake._closed
    assert h.handle in fake._closed


def test_different_names_do_not_collide(fake):
    a = singleton.acquire("Local\\A")
    b = singleton.acquire("Local\\B")
    assert a is not None
    assert b is not None
    assert a.handle != b.handle
