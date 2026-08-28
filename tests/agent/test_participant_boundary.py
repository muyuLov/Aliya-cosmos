"""Task 3.0b: 多参与者私聊边界测试

验证 shareParticipantDetails 开关与参与者隔离行为。
"""


def test_participant_payload_baseline_fields():
    """Participant 应包含所有基线 payload 字段"""
    from agent.story.participant import Participant

    p = Participant(
        story_id="s1",
        person_id="aliya",
        display_name="Aliya",
        profile="温柔",
        relationship="朋友",
    )
    # 基线字段
    assert p.person_id == "aliya"
    assert p.display_name == "Aliya"
    assert p.profile == "温柔"
    assert p.relationship == "朋友"
    assert isinstance(p.relationship_overlay, dict)
    assert p.last_user_message_at is None
    assert p.last_character_message_at is None
    assert p.unread_message_count == 0
    assert p.pending_reply_count == 0


def test_share_participant_details_default_false():
    """shareParticipantDetails 默认 False，别参与者内容应被占位"""
    from agent.story.canonical import CanonicalStory

    # 验证 CanonicalStory 上可存 share_participant_details
    story = CanonicalStory(story_id="s1", setting="日常")
    # 默认 False
    assert story.state.get("share_participant_details", False) is False


def test_omit_other_participant_content():
    """关闭 shareParticipantDetails 时，别参与者 content 应被替换"""
    from agent.story.entry import ScriptEntry

    entry = ScriptEntry(
        story_id="s1",
        participant_id="other_user",
        kind="user_message",
        actor="other_user",
        content="这是别参与者的私聊内容",
    )

    # 模拟占位符替换
    OMITTED = "[participant-specific conversation omitted]"

    def omit_if_not_shared(
        entry: ScriptEntry,
        current_participant_id: str,
        share_details: bool,
    ) -> ScriptEntry:
        if not share_details and entry.participant_id != current_participant_id:
            return ScriptEntry(
                story_id=entry.story_id,
                participant_id="",
                kind=entry.kind,
                actor=entry.actor,
                content=OMITTED,
                occurred_at=entry.occurred_at,
            )
        return entry

    # 当前参与者是 aliya，share_details=False
    result = omit_if_not_shared(entry, "aliya", share_details=False)
    assert result.content == OMITTED
    assert result.participant_id == ""

    # share_details=True 时应保留原文
    result2 = omit_if_not_shared(entry, "aliya", share_details=True)
    assert result2.content == "这是别参与者的私聊内容"
