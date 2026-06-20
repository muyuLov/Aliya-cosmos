from __future__ import annotations

import re
from pathlib import Path

from agent.models import Skill
from core.logger import get_logger

try:
    import yaml
except ImportError:  # pragma: no cover - 运行环境未安装 PyYAML
    yaml = None


logger = get_logger(__name__)
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", re.DOTALL)


class SkillLoader:
    def __init__(self, skills_dir: str | Path = "agent/skills") -> None:
        self.skills_dir = Path(skills_dir)
        self._cached: list[Skill] | None = None

    def load_all(self) -> list[Skill]:
        if self._cached is not None:
            return self._cached

        if not self.skills_dir.exists():
            self._cached = []
            return []

        if yaml is None:
            raise RuntimeError("PyYAML 未安装，无法解析技能文件 frontmatter")

        loaded: list[Skill] = []
        for file_path in sorted(self.skills_dir.glob("*.md")):
            # 跳过非技能文档（如以数字或下划线开头的说明文件）
            if file_path.stem and (file_path.stem[0].isdigit() or file_path.stem[0] == "_"):
                continue
            try:
                skill = self._parse_skill_file(file_path)
            except Exception as exc:
                logger.debug("跳过非技能文件 %s: %s", file_path.name, exc)
                continue

            if skill.enabled:
                loaded.append(skill)
        self._cached = sorted(loaded, key=lambda item: (item.priority, item.name, item.file_path.name))
        logger.info("技能加载完成: count=%d | names=%s", len(self._cached), [s.name for s in self._cached])
        return self._cached

    def reload(self) -> None:
        """清空缓存，下次调用 load_all 时重新从磁盘加载。"""
        self._cached = None

    def _parse_skill_file(self, file_path: Path) -> Skill:
        content = file_path.read_text(encoding="utf-8-sig")
        match = _FRONTMATTER_RE.match(content)
        if not match:
            raise ValueError(f"Skill 文件缺少 frontmatter: {file_path}")

        meta = self._parse_frontmatter(match.group(1))
        body = match.group(2).strip()
        enabled = meta.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Skill enabled 字段必须是布尔值: {file_path}")

        priority = meta.get("priority", 100)
        if not isinstance(priority, int):
            raise ValueError(f"Skill priority 字段必须是整数: {file_path}")

        description = meta.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"Skill description 字段必须是字符串: {file_path}")

        when_to_use = meta.get("when_to_use", "")
        if not isinstance(when_to_use, str):
            when_to_use = ""

        return Skill(
            name=meta["name"],
            description=description,
            version=meta.get("version", "1.0.0"),
            enabled=enabled,
            priority=priority,
            instructions=body,
            file_path=file_path,
            when_to_use=when_to_use,
        )

    def _parse_frontmatter(self, raw_meta: str) -> dict[str, object]:
        if yaml is None:
            raise RuntimeError("PyYAML is required to parse skill frontmatter")

        meta = yaml.safe_load(raw_meta) or {}
        if not isinstance(meta, dict):
            raise ValueError("Skill frontmatter 必须是 YAML 映射")
        return meta
