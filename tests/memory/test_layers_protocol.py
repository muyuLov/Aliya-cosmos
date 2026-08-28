from core.memory.layers import MemoryEntry, MemoryLayer


def test_memory_entry_defaults():
    e = MemoryEntry(id="1", content="内容", source="conv", confidence=0.5, importance=0.5)
    assert e.metadata == {}


def test_layer_is_abstract():
    try:
        MemoryLayer()
    except TypeError:
        return
    raise AssertionError("MemoryLayer 应为抽象类")
