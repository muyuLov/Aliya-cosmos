from core.memory.storage.vector_store import SQLiteVectorStore


async def test_add_and_knn_search(tmp_path):
    db_path = tmp_path / "mem.db"
    store = SQLiteVectorStore(str(db_path), dimension=4)
    await store.add("第一条", [0.1, 0.2, 0.3, 0.4], metadata={"layer": "fact"})
    await store.add("第二条", [0.9, 0.8, 0.7, 0.6], metadata={"layer": "fact"})
    hits = await store.search([0.1, 0.2, 0.3, 0.5], top_k=2)
    assert hits[0].text == "第一条"
    assert hits[0].metadata["layer"] == "fact"
    await store.aclose()


async def test_persistence_across_reopen(tmp_path):
    db_path = tmp_path / "mem.db"
    store = SQLiteVectorStore(str(db_path), dimension=4)
    await store.add("持久化文本", [0.5, 0.5, 0.5, 0.5], metadata={})
    await store.aclose()
    store2 = SQLiteVectorStore(str(db_path), dimension=4)
    hits = await store2.search([0.5, 0.5, 0.5, 0.5], top_k=5)
    assert any(h.text == "持久化文本" for h in hits)
    await store2.aclose()
