from core.memory.layers.canon import CanonLayer


async def test_query_returns_persona_text():
    layer = CanonLayer()
    entries = await layer.query("")
    text = entries[0].content
    # 应包含三个设定文件的标识性内容
    assert "Aliya" in text
    assert "身份" in text or "soul" in text or "tone" in text


async def test_query_includes_soul_and_tone():
    layer = CanonLayer()
    entries = await layer.query("")
    text = entries[0].content
    assert text.strip()  # 非空
    # query 结果应拼接 identity/soul/tone-rules 的读取内容
    assert "##" in text  # 设定文档通常含 markdown 标题


async def test_unknown_query_ignored():
    # query 的 text 参数仅用于接口一致性，Canon 只读返回完整设定
    layer = CanonLayer()
    a = (await layer.query(""))[0].content
    b = (await layer.query("随便问点什么"))[0].content
    assert a == b
