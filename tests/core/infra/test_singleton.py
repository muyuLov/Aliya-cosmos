import pytest

from core.infra.singleton import AsyncSingleton


@pytest.fixture(autouse=True)
def _cleanup():
    AsyncSingleton.clear()
    yield
    AsyncSingleton.clear()


async def test_get_or_create_returns_same_instance():
    calls = []

    async def factory():
        calls.append(1)
        return "instance"

    a = await AsyncSingleton.get_or_create("k", factory)
    b = await AsyncSingleton.get_or_create("k", factory)
    assert a is b
    assert len(calls) == 1


async def test_get_sync_returns_none_for_missing():
    assert AsyncSingleton.get_sync("nope") is None


async def test_clear_specific_key():
    await AsyncSingleton.get_or_create("k", lambda: _a("v"))
    AsyncSingleton.clear("k")
    assert AsyncSingleton.get_sync("k") is None


async def _a(v):
    return v
