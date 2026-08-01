# TTS 模块

语音合成服务，封装多提供商的流式 TTS 接入、分段并发预取流水线与实时音频播放。

## 架构概览

```
__init__.py        ← 公共接口，工厂函数，提供商注册
service.py         ← TTSService：分段流水线合成，管理会话生命周期
text_splitter.py   ← 文本预处理：动作描写过滤、分句、短段合并
models.py          ← TTSRequest / VoiceConfig 数据模型
validation.py      ← 配置参数集中校验
exceptions.py      ← 结构化异常（TTS_001~TTS_005）
constants.py       ← 队列大小、缓冲区帧数等常量

providers/
  base.py          ← TTSProvider 抽象基类 + TTSProviderFactory
  astra.py         ← AstraTTSProvider：自建 Docker HTTP 流式 API
  edge.py          ← EdgeTTSProvider：微软必应语音（免费，无需 Key）

player/
  core.py          ← AudioPlayer：sounddevice + asyncio 流式播放
  format_detector.py ← WAV 头部解析
```

**合成流程**：`TTSRequest` → 文本过滤/分句 → 滑动窗口并发预取 → `TTSProvider`（create/consume/close session）→ `AudioPlayer.feed()` → sounddevice 实时播放。

---

## 核心接口

### `create_from_config()` — 从配置文件创建（推荐）

```python
from core.tts import create_from_config

tts_service, player = create_from_config()  # 默认读取 data/config/main.yml

async with tts_service, player:
    request = TTSRequest(text="你好，我是 Aliya。")
    await player.play_stream(tts_service.synthesize(request))
```

返回 `(TTSService, AudioPlayer)` 元组，均支持 `async with` 自动管理资源。

---

### `TTSService`

#### `synthesize(request)` — 流式合成

```python
from core.tts.models import TTSRequest

request = TTSRequest(
    text="借你吉言，我一定会把 Kane 带回去的。",
    speed=1.0,          # 可选，覆盖 VoiceConfig 中的默认值
)

async for chunk in tts_service.synthesize(request):
    await player.feed(chunk)
await player.drain()
```

- 单句直接合成，跳过分段开销
- 多句自动走滑动窗口并发预取流水线（窗口大小 `prefetch_window`，默认 3）
- 合成前自动过滤括号动作描写和省略号，如 `（微笑）` → 删除

---

### `AudioPlayer`

```python
from core.tts import create_player

player = create_player({
    "sample_rate": 32000,
    "channels": 1,
    "pcm_format": "float32",
    "frames_per_buffer": 1024,
    "play_queue_size": 32,
})

async with player:
    await player.play_stream(tts_service.synthesize(request))  # 一步完成
    # 或分步：
    async for chunk in tts_service.synthesize(request):
        await player.feed(chunk)
    await player.drain()
```

`AudioPlayer` 使用 `threading.Queue` 桥接异步生产者与同步 sounddevice 消费者，不阻塞事件循环。支持自动检测 PCM / WAV / MP3 格式（MP3 需 ffmpeg）。

---

### `TTSRequest` / `VoiceConfig`

```python
from core.tts.models import TTSRequest, VoiceConfig

# VoiceConfig 通常从配置文件加载，作为默认值填充请求中的 None 字段
voice = VoiceConfig(
    avatar_id="aliya_v2",
    speed=1.0,
    languages=["zh", "en"],
)

request = TTSRequest(text="你好世界")
# voice.apply_to_request(request) 自动填充 avatar_id、speed、languages
merged = voice.apply_to_request(request)
```

`TTSRequest` 中显式设置的字段优先，`None` 字段由 `VoiceConfig` 填充。

---

### 直接使用提供商（低级接口）

```python
from core.tts import create_service
from core.tts.models import VoiceConfig

service = create_service(
    provider_name="astra",
    provider_config={"api_url": "http://localhost:5000"},
    voice_config=VoiceConfig(speed=1.0),
)

# 或直接操作 Provider（会话式）
provider = service.provider
session_id = await provider.create_session(request)
async for chunk in provider.consume_session(session_id):
    ...
await provider.close_session(session_id)
```

---

## 提供商

| 提供商 | 注册名 | 说明 | 必要配置 |
|--------|--------|------|----------|
| `AstraTTSProvider` | `"astra"` | 自建 Docker HTTP 流式服务，高音质，支持克隆音色 | `api_url` |
| `EdgeTTSProvider` | `"edge"` | 微软必应语音，免费，无需 Key | `voice`（可选）|

提供商配置存放在 `data/config/TTSProviders.json`，通过 `providers.config_path` 引用：

```json
{
  "astra": {
    "api_url": "http://localhost:5000",
    "default_avatar_id": "aliya_v2",
    "timeout": 60,
    "chunk_size": 4096
  },
  "edge": {
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": "+0%",
    "timeout": 30
  }
}
```

---

## 配置

配置路径：`cosmos.service.tts`（`data/config/main.yml`）

```yaml
cosmos:
  service:
    tts:
      providers:
        name: astra
        config_path: data/config/TTSProviders.json  # 提供商详细配置，支持 ${ENV_VAR} 语法

      voice:
        languages: [zh, en]
        speed: 1.0            # 默认语速（0.1-5.0）

      service:
        prefetch_queue_size: 16     # 预取队列大小
        max_concurrent_creates: 10  # 最大并发创建会话数
        prefetch_window: 3          # 滑动窗口段数

      player:
        sample_rate: 32000          # PCM 模式采样率（Hz）
        channels: 1                 # 声道数（1=单声道）
        pcm_format: float32         # PCM 格式：float32 / int16 / int32 / int8
        frames_per_buffer: 1024     # sounddevice 每次写入帧数
        play_queue_size: 32         # 播放队列大小
        queue_timeout: 300.0        # 队列等待超时（秒）
```

详细参数调优指南见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 文本预处理

```python
from core.tts.text_splitter import filter_actions, split_text

# 过滤动作描写和省略号
filter_actions("好想再一次和他们（眼眶泛红）一起逃出去玩啊……")
# → "好想再一次和他们一起逃出去玩啊"

# 分句（按 。！？ 等标点）+ 短句合并
split_text("你好。我是 Aliya。很高兴认识你！")
# → ["你好。我是 Aliya。", "很高兴认识你！"]
```

`split_text` 返回的每一段独立创建 TTS 会话，段数越少则请求数越少；短于 5 字符的段自动合并到相邻段。

---

（TTS 音频缓存功能已移除）

---

## 异常

所有异常继承 `TTSError`（`StructuredException` 子类），错误码前缀 `TTS_`。

| 异常 | 错误码 | 场景 |
|------|--------|------|
| `TTSProviderNotFoundError` | `TTS_001` | 提供商名称未注册 |
| `TTSConnectionError` | `TTS_002` | 服务连接失败 |
| `TTSRequestError` | `TTS_003` | 创建会话/API 请求失败 |
| `TTSSessionError` | `TTS_004` | 会话消费或释放失败 |
| `TTSConfigError` | `TTS_005` | 配置参数校验不通过 |

---

## 依赖关系

- 内部：`core.logger`、`core.config`、`core.exception`
- 外部：
  - `httpx` — AstraTTS HTTP 客户端
  - `edge-tts` — EdgeTTS 客户端
  - `sounddevice` + `numpy` — 音频播放
  - `imageio-ffmpeg` — MP3 解码（可选，不安装则无法播放 MP3）
  - `pydantic` — 数据模型

