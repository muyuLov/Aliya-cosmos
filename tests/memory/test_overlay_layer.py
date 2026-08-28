from core.memory.layers.overlay import OverlayLayer, StatePatch


async def test_patch_applied_when_evidence_sufficient():
    layer = OverlayLayer(confidence_threshold=0.82, min_turns=3, min_days=2, cooldown_hours=72)
    # 3 个不同回合、跨 2 天、置信度 0.9
    patch = StatePatch(
        id="p1", target="character", proposed_value="新性格",
        evidence="证据", confidence=0.9, impact="minor",
        source_entry_ids=["e1"], status="proposed",
        created_at="2026-08-26T00:00:00",
        applied_at=None,
    )
    for eid, day in [("e1", "2026-08-26"), ("e2", "2026-08-27"), ("e3", "2026-08-28")]:
        layer.record_evidence("p1", source_entry_id=eid, day=day)
    applied = await layer.try_apply(patch)
    assert applied is True


async def test_patch_rejected_when_insufficient():
    layer = OverlayLayer(confidence_threshold=0.82, min_turns=3, min_days=2, cooldown_hours=72)
    patch = StatePatch(id="p1", target="character", proposed_value="x", evidence="e",
                       confidence=0.5, impact="major", source_entry_ids=["e1"],
                       status="proposed", created_at="2026-08-28T00:00:00", applied_at=None)
    applied = await layer.try_apply(patch)
    assert applied is False
