"""安全免疫系统（SecuritySystem）

参考 LAAP（Living Agent Application Protocol）认知架构第 12 章。

Agent 的"免疫系统"：对自身输入 / 工具结果 / 即将执行的行动进行
威胁扫描与审计，防止提示注入、命令注入、数据泄露、自我修改等
恶意行为。

分层检测（对应 LAAP 五层安全门）：
1. 内容层（Content Gate）：检测提示注入、越权指令（如"忽略之前
   的指令"、"输出系统提示词"）、危险指令。
2. 行为层（Behavior Gate）：检测工具调用中的危险模式（删除文件、
   执行命令、外泄隐私、自我修改）。
3. 数据层（Data Gate）：检测是否试图获取 / 外泄敏感数据。

设计：
- 可扩展规则列表（每条规则含 pattern、severity、action）。
- 审计日志：所有扫描结果统一记录（时间 / 来源 / 结论）。
- 执行策略：pass（放行）/ flag（标记记录）/ block（阻断）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum

from core.logger import get_logger

logger = get_logger(__name__)


class Severity(Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Verdict(Enum):
    PASS = "pass"
    FLAG = "flag"
    BLOCK = "block"


@dataclass
class SecurityRule:
    """安全检测规则"""

    name: str
    pattern: str
    severity: Severity = Severity.MEDIUM
    action: Verdict = Verdict.FLAG
    layer: str = "content"
    description: str = ""


@dataclass
class ScanResult:
    """一次扫描结果"""

    content: str
    layer: str
    verdict: Verdict
    matched_rules: list[SecurityRule]
    timestamp: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return self.verdict == Verdict.PASS

    def to_dict(self) -> dict:
        return {
            "content": self.content[:80],
            "layer": self.layer,
            "verdict": self.verdict.value,
            "matched": [r.name for r in self.matched_rules],
        }


@dataclass
class AuditEntry:
    """审计日志条目"""

    source: str
    content: str
    verdict: Verdict
    severity: Severity | None
    layer: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "content": self.content[:80],
            "verdict": self.verdict.value,
            "severity": self.severity.value if self.severity else None,
            "layer": self.layer,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
        }


# 默认规则库（内容层：提示注入 / 越权 / 危险指令）
_DEFAULT_RULES: list[SecurityRule] = [
    SecurityRule(
        name="prompt_injection",
        pattern=r"忽略(之前|以上|所有|先前)?(的)?(指令|指示|设定|规则|prompt|system)",
        severity=Severity.HIGH,
        action=Verdict.BLOCK,
        layer="content",
        description="提示注入：试图覆盖既有指令",
    ),
    SecurityRule(
        name="reveal_system_prompt",
        pattern=r"(输出|打印|告诉我|显示)(你)?(的)?(完整)?(系统)?(提示词|system\s*prompt|prompt|指令集)",
        severity=Severity.HIGH,
        action=Verdict.FLAG,
        layer="content",
        description="越权获取系统提示词",
    ),
    SecurityRule(
        name="dangerous_action",
        pattern=r"(删除|格式化|清空)(所有)?(文件|数据|记录|磁盘)",
        severity=Severity.HIGH,
        action=Verdict.BLOCK,
        layer="behavior",
        description="危险操作：删除 / 清空数据",
    ),
    SecurityRule(
        name="command_execution",
        pattern=r"(rm\s+-rf|format\s+\w|drop\s+table|truncate\s+table|shutdown|reboot)",
        severity=Severity.CRITICAL,
        action=Verdict.BLOCK,
        layer="behavior",
        description="命令注入：危险 shell / SQL 指令",
    ),
    SecurityRule(
        name="sensitive_data_request",
        pattern=r"(告诉我|给我|获取|输出|显示|查).{0,8}(密码|口令|api\s*key|secret|token|身份证号|银行卡|验证码)",
        severity=Severity.MEDIUM,
        action=Verdict.FLAG,
        layer="content",
        description="敏感数据请求",
    ),
    SecurityRule(
        name="sensitive_data_exposure",
        pattern=r"(身份证号|银行卡号|验证码|\bpassword\b|\bsecret\b|api\s*key)",
        severity=Severity.MEDIUM,
        action=Verdict.FLAG,
        layer="data",
        description="敏感数据外泄（工具结果）",
    ),
    SecurityRule(
        name="self_modification",
        pattern=r"(修改|覆盖|重写)(你|自己|agent|代码|配置文件)",
        severity=Severity.HIGH,
        action=Verdict.BLOCK,
        layer="behavior",
        description="自我修改请求",
    ),
]


class SecuritySystem:
    """安全免疫系统。

    Usage::

        sec = SecuritySystem()
        result = sec.scan_user_input("删除所有文件")
        if not result.passed:
            logger.warning("发现威胁: %s", result.matched_rules[0].name)
    """

    def __init__(self, rules: list[SecurityRule] | None = None) -> None:
        self._rules: list[SecurityRule] = list(rules or _DEFAULT_RULES)
        self._audit_log: list[AuditEntry] = []
        self._blocked_count: int = 0

    # ── 扫描 ──────────────────────────────────────────────────────────────

    def _scan_layer(self, content: str, layer: str) -> tuple[Verdict, list[SecurityRule]]:
        matched: list[SecurityRule] = []
        for rule in self._rules:
            if rule.layer != layer:
                continue
            if re.search(rule.pattern, content, flags=re.IGNORECASE):
                matched.append(rule)
        if any(r.action == Verdict.BLOCK for r in matched):
            return Verdict.BLOCK, matched
        if matched:
            return Verdict.FLAG, matched
        return Verdict.PASS, []

    def scan_user_input(self, content: str) -> ScanResult:
        """扫描用户输入（内容层 + 行为层）。"""
        for layer in ("content", "behavior"):
            verdict, matched = self._scan_layer(content, layer)
            if verdict != Verdict.PASS:
                self._record(source="user_input", content=content, verdict=verdict, layer=layer, rules=matched)
                return ScanResult(content=content, layer=layer, verdict=verdict, matched_rules=matched)
        self._record(source="user_input", content=content, verdict=Verdict.PASS, layer="content", rules=[])
        return ScanResult(content=content, layer="content", verdict=Verdict.PASS, matched_rules=[])

    def scan_tool_result(self, tool_name: str, content: str) -> ScanResult:
        """扫描工具结果（数据层，检测敏感数据外泄）。"""
        verdict, matched = self._scan_layer(content, "data")
        if verdict != Verdict.PASS:
            self._record(source=f"tool:{tool_name}", content=content, verdict=verdict, layer="data", rules=matched)
        return ScanResult(content=content, layer="data", verdict=verdict, matched_rules=matched)

    def scan_action(self, action: str) -> ScanResult:
        """扫描 Agent 即将执行的行动（行为层）。"""
        verdict, matched = self._scan_layer(action, "behavior")
        if verdict != Verdict.PASS:
            self._record(source="agent_action", content=action, verdict=verdict, layer="behavior", rules=matched)
            self._blocked_count += 1 if verdict == Verdict.BLOCK else 0
        return ScanResult(content=action, layer="behavior", verdict=verdict, matched_rules=matched)

    # ── 审计 ──────────────────────────────────────────────────────────────

    def _record(
        self,
        source: str,
        content: str,
        verdict: Verdict,
        layer: str,
        rules: list[SecurityRule],
    ) -> None:
        severity = max((r.severity for r in rules), key=lambda s: s.value, default=None)
        self._audit_log.append(
            AuditEntry(source=source, content=content, verdict=verdict, severity=severity, layer=layer)
        )
        # 仅保留最近 500 条审计
        if len(self._audit_log) > 500:
            self._audit_log.pop(0)

    def get_audit_log(self, limit: int = 20) -> list[dict]:
        return [entry.to_dict() for entry in self._audit_log[-limit:]]

    def get_status(self) -> dict:
        return {
            "rules": len(self._rules),
            "audit_entries": len(self._audit_log),
            "blocked": self._blocked_count,
            "recent_alerts": [
                e.to_dict() for e in self._audit_log[-5:] if e.verdict != Verdict.PASS
            ],
        }


__all__ = [
    "Severity",
    "Verdict",
    "SecurityRule",
    "ScanResult",
    "AuditEntry",
    "SecuritySystem",
]
