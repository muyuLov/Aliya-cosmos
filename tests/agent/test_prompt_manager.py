"""测试 PromptManager — 分层 Prompt 加载与组装"""

from __future__ import annotations

from agent.prompts import PromptManager, get_prompt_manager, ALL_STYLES


class TestPromptManager:
    """测试 PromptManager 基本功能"""

    def test_singleton(self):
        """get_prompt_manager 返回同一实例"""
        pm1 = get_prompt_manager()
        pm2 = get_prompt_manager()
        assert pm1 is pm2

    def test_list_styles(self):
        """列出所有风格"""
        pm = PromptManager()
        styles = pm.list_styles()
        assert "default" in styles
        assert "lively" in styles
        assert len(styles) >= 4

    def test_build_soul_system_prompt_default(self):
        """默认风格灵魂 prompt"""
        pm = PromptManager()
        prompt = pm.build_soul_system_prompt(style="default")
        assert len(prompt) > 200
        assert prompt.count("---") >= 3
        assert "Aliya" in prompt or "阿莉娅" in prompt

    def test_build_soul_system_prompt_lively(self):
        """活泼风格灵魂 prompt"""
        pm = PromptManager()
        prompt = pm.build_soul_system_prompt(style="lively")
        assert len(prompt) > 200
        assert "活泼" in prompt or "元气" in prompt

    def test_build_soul_system_prompt_healing(self):
        """治愈风格灵魂 prompt"""
        pm = PromptManager()
        prompt = pm.build_soul_system_prompt(style="healing")
        assert len(prompt) > 200
        assert "治愈" in prompt or "安心" in prompt

    def test_build_soul_system_prompt_sweet(self):
        """撒娇风格灵魂 prompt"""
        pm = PromptManager()
        prompt = pm.build_soul_system_prompt(style="sweet")
        assert len(prompt) > 200
        assert "撒娇" in prompt or "亲近" in prompt or "黏人" in prompt

    def test_build_soul_system_prompt_tone_override(self):
        """风格覆盖：tone_override 替换 tone-rules.md"""
        pm = PromptManager()
        override = "【用户自定义语气】请用简短有力的方式回复。"
        prompt = pm.build_soul_system_prompt(tone_override=override)
        assert override in prompt

    def test_build_tool_system_prompt(self):
        """工具阶段 prompt"""
        pm = PromptManager()
        prompt = pm.build_tool_system_prompt()
        assert len(prompt) > 50
        assert "工具" in prompt

    def test_build_emotion_patch_empty(self):
        """空情绪返回空字符串"""
        pm = PromptManager()
        assert pm.build_emotion_patch("") == ""

    def test_build_emotion_patch_known(self):
        """已知情绪返回非空补丁"""
        pm = PromptManager()
        assert len(pm.build_emotion_patch("开心")) > 0
        assert len(pm.build_emotion_patch("难过")) > 0

    def test_build_emotion_patch_unknown(self):
        """未知情绪返回空字符串"""
        pm = PromptManager()
        assert pm.build_emotion_patch("不存在") == ""

    def test_build_emotion_patch_calm(self):
        """平静情绪无补丁"""
        pm = PromptManager()
        assert pm.build_emotion_patch("平静") == ""

    def test_get_config_dict(self):
        """配置字典包含必需字段"""
        pm = PromptManager()
        cfg = pm.get_config_dict()
        assert "styles" in cfg
        assert "soul_chars" in cfg
        assert cfg["current_style"] == "default"

    def test_clear_cache(self):
        """清空缓存后重新加载"""
        pm = PromptManager()
        before = pm.build_soul_system_prompt()
        pm.clear_cache()
        after = pm.build_soul_system_prompt()
        assert before == after  # 内容应一致

    def test_invalid_style_falls_back_to_default(self):
        """无效风格回退到 default"""
        pm = PromptManager()
        prompt = pm.build_soul_system_prompt(style="does_not_exist")  # type: ignore[arg-type]
        assert len(prompt) > 200

    def test_missing_file_graceful(self):
        """文件缺失时静默返回空字符串"""
        pm = PromptManager()
        result = pm._load("nonexistent_file.md")
        assert result == ""
