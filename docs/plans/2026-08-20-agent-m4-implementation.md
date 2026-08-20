# Aliya-cosmos Agent M4 实施计划：多渠道接入与整体打磨

> 里程碑定位：M1=基础对话闭环，M2=伴侣能力（情绪/主动/多会话），M3=知识外扩（RAG/Skill/MCP）。
> **M4 = 多渠道**：让同一「大脑」（同一 `AgentSession`/`AgentLoop`）能被飞书、微信等外部渠道驱动，消息双向往来；并对 M1–M3 做整体打磨（配置装配收敛、错误降级、测试覆盖）。
> 对标架构文档 D3（渠道复用同一大脑，仅替换事件源与 EventSink）+ D4（双层事件模型）+ D7（渠道凭据安全）。
> 核心抽象已存在于代码：`agent/events.py` 的 `EventSink` 接口、`agent/session.py` 的 `AgentSession.submit()` 异步生成器、`agent/loop.py` 已 yield `CONFIRM_REQUEST` 等事件。M4 不重复造轮子，只补「sink 注册」与「渠道驱动」两处。

---

## 0. 集成点总览（本文档所有任务前提）

| 组件 | 真实接口（已存在） | M4 用法 |
|------|------|------|
| 会话提交 | `AgentSession.submit(text) -> AsyncGenerator[AgentEvent\|ProtocolEvent]` | 渠道收到消息 → `async for event in session.submit(text)` |
| 事件 sink | `agent/events.py`：`EventSink`（Protocol，`async def emit(event: AgentEvent)`）；`to_protocol(event)` 已把 `AgentEvent` 映射为 `ProtocolEvent` | 渠道实现 `EventSink`，订阅进程内事件转发到飞书/微信 |
| 确认原语 | `AgentLoop.resolve_confirmation(call_id, allowed)`（`loop.py:74`）；`CONFIRM_REQUEST` 事件 payload=`{tool_name, call_id, arguments}` | 渠道 sink 收到 `CONFIRM_REQUEST` → 在渠道呈现确认交互 → 用户操作后回调 `resolve_confirmation` |
| 会话装配 | `agent/ws.py:146` `_default_session_factory()` 内联创建 `service/memory/registry/loop/session` | M4 抽出 `build_agent_session(conversation_id)` 供 WS、飞书、微信共用，消除重复装配 |
| 配置 | `data/config/main.yml` 已有 `cosmos.service.agent` 段；**新增** `cosmos.channels.{feishu,wechat}.*` | 渠道凭据/开关走配置，不硬编码 |
| 工具风险 | `ToolDefinition.risk`（M3 已用）：`safe`/`medium`/`high` | 飞书/微信渠道对 `risk=high` 工具强制确认交互（M4 护栏） |

**关键约束**：
- 渠道**不改动** `AgentLoop` 两阶段逻辑、`AgentSession.submit` 契约、WS 协议、情绪引擎、RAG/Skill/MCP。
- 渠道**复用** `submit()` 与 `EventSink`，仅做「事件源替换」与「事件消费器替换」。
- 凭据（飞书 app_secret、微信 token）从配置读取，遵循 D7 不明文落盘原则（配置本身已走 `main.yml`，不额外写密钥文件）。

---

## Part A：抽象层（渠道无关的复用基座）

> 目标：让 WS 网关、飞书、微信共用同一装配与同一事件消费模型，避免三处重复。

### A1. 抽出会话装配工厂 `agent/session.py`

把 `agent/ws.py:149-185` 的内联装配逻辑搬到 `agent/session.py`，新增 `build_agent_session(conversation_id) -> AgentSession`（异步）与 `build_session_factory() -> SessionFactory`（兼容 WS 现有 `SessionFactory` 类型）。

```python
# agent/session.py —— 新增（装配逻辑从 ws.py 迁移，行为不变）
from typing import Callable
from agent.events import AgentEvent, ProtocolEvent  # 保持现有 import

SessionFactory = Callable[[str], Awaitable[AgentSession]]  # 与 ws.py 原 SessionFactory 一致（迁移后 ws.py 改为 from agent.session import SessionFactory）


async def build_agent_session(conversation_id: str) -> AgentSession:
    """生产装配：真实 LLM 服务 + GRAG 记忆 + 内置工具 + 情绪引擎（若有）。

    从 agent/ws.py 的 _default_session_factory 迁移，行为完全一致；
    M3 的索引/ MCP 同步在此处 await（见 M3 D1）。
    """
    from agent.context import ContextBuilder
    from agent.loop import AgentLoop
    from agent.tools import PermissionChecker, ToolRegistry
    from agent.tools.builtin import register_builtin_tools
    from core.config import get_config_instance
    from core.llm import create_from_config
    from core.logger import get_logger
    from core.memory.memory_manager import GRAGMemoryManager

    logger = get_logger(__name__)
    cfg = get_config_instance("data/config/main.yml")
    service = create_from_config("data/config/main.yml", conversation_id=conversation_id)

    memory = None
    try:
        memory = GRAGMemoryManager()
    except Exception as exc:
        logger.warning("GRAG 记忆初始化失败，工具将降级: %s", exc)

    registry = ToolRegistry()
    register_builtin_tools(registry)

    perm_path = cfg.get("cosmos.service.agent.permissions.config_path", "data/config/Permissions.yml")
    checker = PermissionChecker(perm_path)

    loop = AgentLoop(
        service=service, registry=registry, checker=checker,
        context=ContextBuilder(), memory=memory,
    )
    return AgentSession(conversation_id, service, loop)


def build_session_factory() -> SessionFactory:
    """返回兼容 WS 的 SessionFactory（无参工厂闭包）。"""
    async def factory(cid: str) -> AgentSession:
        return await build_agent_session(cid)
    return factory
```

**验证**：`tests/test_session_factory.py`
- `await build_agent_session("test-cid")` 返回 `AgentSession`，`session.loop` 为 `AgentLoop` 实例；
- WS 网关改用 `build_session_factory()` 后既有 WS 测试（M1）无回归。

### A2. `AgentSession` 增加 EventSink 注册

`AgentSession` 当前 `submit()` 只 yield 事件，无人订阅。新增 sink 注册，使进程内事件可被外部消费者（渠道）订阅。

```python
# agent/session.py —— AgentSession.__init__ 增加
from agent.events import AgentEvent, EventSink, ProtocolEvent  # 新增 EventSink

class AgentSession:
    def __init__(self, conversation_id, service, loop) -> None:
        ...
        self._sinks: list[EventSink] = []

    def add_sink(self, sink: EventSink) -> None:
        """注册事件订阅者（如飞书/微信渠道）。"""
        self._sinks.append(sink)

    def remove_sink(self, sink: EventSink) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    async def _emit_to_sinks(self, event: AgentEvent | ProtocolEvent) -> None:
        # 进程内事件（AgentEvent）与线上协议事件（ProtocolEvent，含 CONFIRM_REQUEST）均广播给渠道；
        # CONFIRM_REQUEST 以 ProtocolEvent 形式 yield（见 loop.py:174），故需两种都广播。
        for sink in self._sinks:
            try:
                await sink.emit(event)
            except Exception as exc:  # pragma: no cover - sink 故障不阻断主流程
                logger.warning("EventSink.emit 失败（已隔离）: %s", exc)

    async def submit(self, text: str) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        async for event in self._loop.submit_user_message(text):
            await self._emit_to_sinks(event)  # 全部事件广播给渠道 sink（sink 自行挑选关心类型）
            yield event
```

**验证**：`tests/test_session_sink.py`
- 注入一个 `FakeSink`（记录收到的 `AgentEvent`），`async for _ in session.submit("hi")` 后 `FakeSink` 收到 `RunStarted`/`TextMessageDelta`/`RunFinished`；
- sink 抛异常时 `submit` 不中断（隔离）；`remove_sink` 后不再收到事件。

### A3. WS 网关改用工厂（增量改造）

`agent/ws.py` 的 `_default_session_factory` 改为直接返回 `build_session_factory()`；并删除 ws.py 第28行局部 `SessionFactory = Callable[...]` 定义，改为 `from agent.session import SessionFactory`（避免两处重复定义）。`run_agent` 逻辑不变（WS 仍用 queue 自行消费事件流，不强制改用 sink，保持增量）。

**验证**：M1 既有 WS 测试无回归；`create_ws_router()` 默认工厂来自 `build_session_factory()`；ws.py 不再重复定义 `SessionFactory`。

---

## Part B：飞书渠道

> 对标 Cyrene/claude-code 的飞书双向消息。M4 采用**简化稳健**方案：飞书开放平台「事件订阅」webhook 接收消息 + 主动推送 API 回复（不依赖社区 MCP 插件，避免外部依赖）。
> 安全：app_id/app_secret/webhook 从 `cosmos.channels.feishu.*` 读取（D7）。

### B1. 飞书配置段 `data/config/main.yml`

```yaml
cosmos:
  channels:
    feishu:
      enabled: false              # 默认关闭，显式开启
      app_id: ""                  # 飞书应用 app_id
      app_secret: ""              # 飞书应用 app_secret（不落盘明文密钥文件）
      verification_token: ""      # 事件订阅校验 token
      event_path: "/channels/feishu"   # 接收 webhook 的路由路径
      confirm_via_feishu: true    # 高风险工具是否经飞书交互确认
```

### B2. 飞书客户端 `agent/channels/feishu_client.py`

封装飞书开放 API：①获取 tenant_access_token（app_id/secret 换票）②发送消息（text/卡片）③处理 URL 校验 challenge。用 `httpx.AsyncClient`。

```python
"""飞书开放平台客户端：token 获取 + 消息发送 + 事件校验。"""
from __future__ import annotations

import httpx
from core.logger import get_logger

logger = get_logger(__name__)
_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_FEISHU_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str | None = None

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.post(_FEISHU_TOKEN_URL, json={"app_id": self._app_id, "app_secret": self._app_secret})
            data = resp.json()
            self._token = data["tenant_access_token"]
            return self._token

    async def send_text(self, chat_id: str, text: str) -> None:
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_FEISHU_SEND_URL}?receive_id_type=chat_id",
                json={"receive_id": chat_id, "msg_type": "text", "content": _text_content(text)},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200 or resp.json().get("code") != 0:
                logger.warning("飞书消息发送失败: %s", resp.text)

    async def send_card(self, chat_id: str, card: dict) -> None:
        """发送交互卡片（确认请求用，含确认/拒绝按钮）。"""
        token = await self._ensure_token()
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{_FEISHU_SEND_URL}?receive_id_type=chat_id",
                json={"receive_id": chat_id, "msg_type": "interactive", "content": _card_content(card)},
                headers={"Authorization": f"Bearer {token}"},
            )


# 飞书 API 约束：content 必须是 JSON 字符串（非 dict），故需 json.dumps
def _text_content(text: str) -> str:
    import json as _json
    return _json.dumps({"text": text}, ensure_ascii=False)


def _card_content(card: dict) -> str:
    import json as _json
    return _json.dumps(card, ensure_ascii=False)
```

**验证**：`tests/test_feishu_client.py`
- 用 `respx`/`httpx_mock` 桩 token 与 send 接口，`send_text` 触发正确请求；
- token 缓存生效（第二次不发 token 请求）；发送失败仅告警不抛异常。

### B3. 飞书事件 sink `agent/channels/feishu_sink.py`

实现 `EventSink`，把进程内事件转发到飞书聊天：流式文本累积后发送；`CONFIRM_REQUEST` 发交互卡片。

```python
"""飞书事件消费者：实现 EventSink，把 AgentEvent 转发到飞书会话。"""
from __future__ import annotations

from agent.events import AgentEvent, EventSink, ProtocolEvent, TextMessageDelta, TextMessageEnd, CONFIRM_REQUEST
from core.logger import get_logger

logger = get_logger(__name__)


class FeishuEventSink(EventSink):
    def __init__(self, client: "FeishuClient", chat_id: str, confirm: bool = True) -> None:
        self._client = client
        self._chat_id = chat_id
        self._confirm = confirm
        self._buf = ""

    async def emit(self, event: AgentEvent | ProtocolEvent) -> None:
        # CONFIRM_REQUEST 是 ProtocolEvent（loop.py:174），需优先判定
        if self._confirm and isinstance(event, ProtocolEvent) and event.type == CONFIRM_REQUEST:
            await self._client.send_card(self._chat_id, _confirm_card(event.payload))
            return
        if isinstance(event, TextMessageDelta):
            self._buf += event.text  # 累积，结束再发（飞书非流式，避免刷屏）
        elif isinstance(event, TextMessageEnd):
            full = event.full_text or self._buf
            if full.strip():
                await self._client.send_text(self._chat_id, full)
            self._buf = ""
        # 其他事件（RunStarted/StepStarted/TOOL_CALL_* 等）忽略

# 辅助：构造确认卡片，把 call_id + chat_id 编码进交互按钮 value（供 /card 回调解出）
def _confirm_card(payload: dict) -> dict:
    return {
        "type": "template_card", "data": {
            "template_card": {
                "card_type": "button_interaction",
                "main": {"title": "工具授权确认", "sub_title": f"是否允许执行：{payload.get('tool_name')}"},
                "action": {"button_list": [
                    {"text": "确认", "value": {"call_id": payload.get("call_id"), "chat_id": "_CHAT_ID_", "allowed": True}},
                    {"text": "拒绝", "value": {"call_id": payload.get("call_id"), "chat_id": "_CHAT_ID_", "allowed": False}},
                ]},
            }
        }
    }  # 实际实现中 _CHAT_ID_ 用 self._chat_id 替换

> 类型说明：`AgentSession._emit_to_sinks` 对 `AgentEvent` 与 `ProtocolEvent` 均广播（A2 已修正），sink 自行挑选关心类型。`FeishuEventSink.emit` 签名放宽为 `AgentEvent | ProtocolEvent` 以匹配广播。
            logger.warning("EventSink.emit 失败（已隔离）: %s", exc)
```

`FeishuEventSink.emit` 签名同步放宽为 `async def emit(self, event: AgentEvent | ProtocolEvent)`。

**验证**：`tests/test_feishu_sink.py`
- 构造 `FakeFeishuClient`（记录 send_text 调用），emit 一串 `TextMessageDelta` + `TextMessageEnd` 后，`FakeFeishuClient` 收到聚合的完整文本（非逐字）；
- emit `ProtocolEvent(type=CONFIRM_REQUEST, ...)` 时触发 `send_card`（若 `confirm=True`）。

### B4. 飞书 Webhook 路由 `agent/channels/feishu_router.py`

FastAPI 路由：①`GET/POST event_path` 处理飞书校验 challenge 与消息事件；②消息事件解析出 `chat_id` + `text` → 取/建 `AgentSession`（按 chat_id 维度，每飞书会话一个 `AgentSession`）→ `submit(text)` 并绑定 `FeishuEventSink`；③卡片按钮回调解析 `call_id` + 操作 → `resolve_confirmation`。

```python
"""飞书 webhook 路由：接收消息事件、驱动 AgentSession、处理确认卡片回调。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from agent.session import AgentSession, build_agent_session
from agent.channels.feishu_client import FeishuClient
from agent.channels.feishu_sink import FeishuEventSink
from core.logger import get_logger

logger = get_logger(__name__)


def create_feishu_router(client: FeishuClient, confirm: bool = True) -> APIRouter:
    router = APIRouter()
    sessions: dict[str, AgentSession] = {}

    @router.post("/channels/feishu")
    async def feishu_webhook(req: Request):
        body = await req.json()
        if body.get("type") == "url_verification":
            return {"challenge": body["challenge"]}  # 飞书校验
        event = body.get("event", {})
        msg = event.get("message", {})
        chat_id = msg.get("chat_id") or event.get("open_chat_id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            return {"code": 0}
        session = sessions.get(chat_id)
        if session is None:
            session = await build_agent_session(chat_id)
            session.add_sink(FeishuEventSink(client, chat_id, confirm=confirm))  # 每会话绑定一次
            sessions[chat_id] = session
        async for _ in session.submit(text):
            pass  # 事件已由 sink 转发到飞书，此处消费以驱动生成
        return {"code": 0}

    @router.post("/channels/feishu/card")
    async def feishu_card_callback(req: Request):
        """飞书交互卡片回调：用户点击确认/拒绝按钮。"""
        body = await req.json()
        action = body.get("action", {})
        value = action.get("value", {})
        call_id = value.get("call_id", "")
        allowed = bool(value.get("allowed", False))
        session = sessions.get(value.get("chat_id", ""))
        if session is None or not call_id:
            return {"code": 0}
        await session.loop.resolve_confirmation(call_id, allowed=allowed)
        return {"code": 0}

    return router
```

**验证**：`tests/test_feishu_router.py`
- 用 `fastapi.TestClient` POST 一条消息事件（桩 `FeishuClient`），断言 `FeishuClient.send_text` 被调用且内容含 Agent 回复；
- `url_verification` 返回 challenge；空消息/无 chat_id 安全返回。

---

## Part C：微信渠道

> 微信个人号无官方 Bot API，M4 采用**稳健可测**方案：复用现有「WS/HTTP 网关」思维，提供微信适配层——通过配置化的「消息入站适配器」（企业微信应用消息 API 或本地桥接）接收文本，复用 `AgentSession.submit` + `EventSink`。
> 为克制范围，M4 微信实现**与飞书同构**：`WeChatClient` + `WeChatEventSink` + `create_wechat_router()`，仅替换 API 端点与消息格式。不实现扫码登录/OAuth（属后续里程碑）。

### C1. 微信配置段 `data/config/main.yml`

```yaml
    wechat:
      enabled: false
      corp_id: ""          # 企业微信 corp_id（或本地桥接标识）
      agent_id: ""
      secret: ""           # 应用 secret
      token: ""            # 回调校验 token
      aes_key: ""          # 消息加密 aes_key
      event_path: "/channels/wechat"
      confirm_via_wechat: true
```

### C2. `agent/channels/wechat_client.py` + `wechat_sink.py` + `wechat_router.py`

与 B2/B3/B4 **同构**，仅替换端点和消息格式（`msg_type` 用企业微信 `text`/`template_card`）。代码模式完全一致，不复述，落地时按飞书模板平移。

**验证**：`tests/test_wechat_*.py` 与飞书对应测试同构（桩 client + sink + router）。

---

## Part D：整体打磨

### D1. 渠道装配入口 `agent/channels/__init__.py`

导出 `create_feishu_router` / `create_wechat_router` / `load_channel_configs()`（读 `cosmos.channels.*`，返回已启用渠道列表）/`build_channel_routers()`（供 `agent/app.py` 挂载）。

```python
# agent/channels/__init__.py
from agent.channels.feishu_client import FeishuClient
from agent.channels.feishu_router import create_feishu_router
from agent.channels.wechat_router import create_wechat_router  # Part C
from core.config import get_config_instance
from fastapi import APIRouter


def load_channel_configs() -> dict:
    cfg = get_config_instance("data/config/main.yml")
    return cfg.get("cosmos.channels") or {}


def build_channel_routers() -> list[APIRouter]:
    """按配置构造已启用渠道的路由（feishu/wechat）。"""
    channels = load_channel_configs()
    routers: list[APIRouter] = []
    feishu = channels.get("feishu") or {}
    if feishu.get("enabled"):
        client = FeishuClient(feishu["app_id"], feishu["app_secret"])
        routers.append(create_feishu_router(client, confirm=feishu.get("confirm_via_feishu", True)))
    wechat = channels.get("wechat") or {}
    if wechat.get("enabled"):
        # Part C 同构构造 WeChatClient + create_wechat_router
        routers.append(create_wechat_router(client=_build_wechat_client(wechat), confirm=wechat.get("confirm_via_wechat", True)))
    return routers
```

### D2. 启动装配（应用入口 `agent/app.py`）

`agent/app.py` 的 `create_app()` 当前只挂 `create_ws_router()`（见 `agent/app.py:13`）。M4 按配置挂载渠道：

```python
# agent/app.py —— create_app 内补充
from agent.channels import load_channel_configs, build_channel_routers

def create_app() -> FastAPI:
    app = FastAPI(title="Aliya Agent")
    app.include_router(create_ws_router())  # 本地 GUI 仍走 WS
    for router in build_channel_routers():  # 按 cosmos.channels.* 配置挂载已启用渠道
        app.include_router(router)
    return app
```

`build_channel_routers()`（在 `agent/channels/__init__.py` 定义）读取 `load_channel_configs()`，对 `enabled=true` 的渠道构造 `FeishuClient` 并 `create_feishu_router(client, confirm=...)`（微信同构）。WS 路由保留。

**验证**：`feishu.enabled=false` 时应用启动不挂载飞书路由（无凭据不报错）；`enabled=true` 且凭据齐备时挂载 `/channels/feishu` 与 `/channels/feishu/card`；WS 路由仍可用。

### D3. 错误降级与护栏

- **sink 故障隔离**：A2 已保证 `emit` 异常不阻断 `submit`。
- **渠道断开重连**：架构文档要求"渠道断开自动重连，会话状态保留"——M4 采用进程内会话缓存（`sessions` dict 按 chat_id），重连后复用同一 `AgentSession`，历史不丢。
- **高风险工具护栏**：确认判定仍在 `AgentLoop`（`PermissionChecker` 返回 `CONFIRM` 时 yield `CONFIRM_REQUEST`，见 `loop.py:171`）；渠道 sink 收到 `CONFIRM_REQUEST` 后呈现为飞书/微信交互卡片，用户点击确认/拒绝时经 `resolve_confirmation(call_id, allowed)` 回调完成授权（B4/C2 卡片回调端点）。`safe`/`medium` 工具按既有 `PermissionChecker` 流程，不经卡片确认。
- **记忆/向量不可用**：沿用 core 告警降级（A1 已含 `GRAGMemoryManager` try 隔离）。

### D4. 测试与验收

- 单测：A1/A2/A3/B2/B3/B4/C2 各模块（桩 HTTP + FakeSink）。
- 集成：内存 transport 模拟「飞书消息 → AgentSession → sink → 飞书回复」全链路（参考 M1 集成测试风格）。
- 验收：配置 `feishu.enabled=true` + 真实 app 凭据后，飞书发消息收到 Aliya 回复；`CONFIRM_REQUEST` 出现时飞书收到确认卡片。

---

## 验证清单（M4 完成标准）

| 能力 | 验证项 |
|------|--------|
| 复用基座 | `build_agent_session` 被 WS/飞书/微信共用；WS 既有测试无回归 |
| EventSink | `AgentSession.add_sink` 后进程内事件（含 `CONFIRM_REQUEST`）广播到渠道；sink 故障隔离 |
| 飞书 | webhook 收消息 → `submit` → sink 转发回复；`CONFIRM_REQUEST` 触发卡片确认回调 |
| 微信 | 同构可达（端点/格式替换） |
| 护栏 | `risk=high` 工具经渠道交互确认；会话按 chat_id 缓存，重连不丢历史 |
| 配置 | 凭据走 `cosmos.channels.*`，默认关闭，不硬编码、不额外密钥文件 |
| 契约 | `AgentLoop`/`submit`/WS 协议/情绪引擎/RAG/Skill/MCP **未改动** |

---

## 实施顺序建议（每步可独立验证）

```
A1 装配工厂      → 单测 test_session_factory + WS 无回归
A2 sink 注册     → 单测 test_session_sink
A3 WS 改用工厂   → M1 WS 测试无回归
B1 飞书配置      → 配置段落地
B2 飞书客户端     → 单测 test_feishu_client
B3 飞书 sink     → 单测 test_feishu_sink
B4 飞书路由      → 单测 test_feishu_router
C1-C2 微信同构   → 单测 test_wechat_*
D1-D4 装配打磨   → 集成测试 + 配置验收
```

> 范围克制说明：M4 **不实现** GUI 渠道管理界面、微信扫码登录/OAuth 网页授权、MCP channel 插件（claude-code 那种）、飞书群组/文件附件透传。这些属后续里程碑。M4 目标是：同一大脑可被飞书/微信驱动，消息双向往来，并对 M1–M3 做装配收敛与降级打磨。
