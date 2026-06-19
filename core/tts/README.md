# TTS 模块

负责文本转语音的统一服务封装，支持多提供商切换、会话式流式合成与实时音频播放，集成配置、日志与异常系统。

## 核心接口

| 接口 | 说明 |
| ---- | ---- |
| `create_service(provider_name, provider_config, voice_config, prefetch_queue_size, max_concurrent_creates)` | 创建 TTSService 实例 |
| `create_player(player_config)` | 创建 AudioPlayer 实例 |
| `create_from_config(config_path, config_prefix)` | 从配置文件一键创建 TTSService 与 AudioPlayer |
| `TTSService.synthesize(request)` | 流式合成，文本自动分段，按原文顺序 yield 音频块 |
| `AudioPlayer.play_stream(chunks)` | 接收异步迭代器并实时播放 |
| `AudioPlayer.feed(chunk)` / `drain()` | 细粒度控制：逐块送入 / 等待播放完毕 |
| `AudioPlayer.pause()` / `resume()` / `stop()` | 播放控制：暂停 / 恢复 / 停止 |
| `AudioPlayer.set_volume(volume)` / `get_volume()` | 音量控制：设置 / 获取音量（0.0-1.0） |
| `AudioPlayer.get_progress()` | 获取播放进度信息 |
| `AudioPlayer.set_progress_callback(callback)` | 设置进度回调函数 |
| `TTSProviderFactory.register(name, cls)` | 注册自定义提供商 |

### 使用示例

```python
from core.tts import TTSRequest, create_from_config

# 一键从配置文件加载
service, player = create_from_config()

async def speak(text: str):
    async with service, player:
        await player.play_stream(service.synthesize(TTSRequest(text=text)))
```

也可以手动组装：

```python
from core.tts import TTSRequest, VoiceConfig, create_player, create_service

service = create_service("astra", {"api_url": "http://127.0.0.1:5000"}, VoiceConfig(speed=1.0))
player = create_player({"sample_rate": 32000, "pcm_format": "float32"})

async def speak(text: str):
    async with service, player:
        await player.play_stream(service.synthesize(TTSRequest(text=text)))
```

## 配置说明

`data/config/main.yml` 中 `cosmos.service.tts` 节点：

```yaml
tts:
  provider: astra          # TTS 提供商名称
  astra:
    api_url: http://127.0.0.1:5000       # AstraTTS 服务地址
    default_avatar_id: chenxing          # 默认音色 ID
    timeout: 60                          # 请求超时时间（秒）
  voice:
    speed: 1.0             # 语速（1.0=正常，范围 0.1~5.0）
    languages: ["zh", "en"]  # 支持的语言列表
  service:
    prefetch_queue_size: 16       # 预取队列大小（范围: 1-256，默认: 16）
    max_concurrent_creates: 10    # 最大并发创建会话数（范围: 1-100，默认: 10）
  player:
    sample_rate: 32000     # 采样率（Hz，范围: 8000-192000）
    pcm_format: float32    # 音频格式: float32 / int16 / int32 / int8
    channels: 1            # 声道数: 1=单声道, 2=立体声
    frames_per_buffer: 1024  # pyaudio 缓冲区大小（帧数，范围: 64-8192）
    play_queue_size: 32    # 播放队列大小（范围: 1-1024）
    queue_timeout: 300.0   # 队列超时（秒，null 表示无限等待）
    progress_interval: 0.1 # 进度回调最小时间间隔（秒）
```

## 依赖关系

- 内部模块：`core.config`、`core.exception`、`core.logger`
- 外部库：`httpx`、`pydantic`、`pyaudio`、`numpy`

## 异常处理

模块专用异常定义在 `core/tts/exceptions.py`，均继承 `TTSError`：

| 异常类 | 错误码 | 触发场景 |
|--------|--------|----------|
| `TTSProviderNotFoundError` | `TTS_001` | `TTSProviderFactory.create()` 找不到指定提供商 |
| `TTSConnectionError` | `TTS_002` | TTS 服务连接失败 |
| `TTSRequestError` | `TTS_003` | API 请求失败（创建会话、消费流） |
| `TTSSessionError` | `TTS_004` | 会话管理错误（不存在、释放失败） |
| `TTSConfigError` | `TTS_005` | 配置参数无效（参数范围错误、格式不支持等） |
| `AudioPlayerError` | `TTS_010` | 音频播放线程异常 |

```python
from core.tts.exceptions import TTSRequestError, TTSSessionError, TTSConfigError

try:
    async with service, player:
        await player.play_stream(service.synthesize(TTSRequest(text="你好")))
except TTSRequestError as e:
    print(e.code, e.details)   # TTS_003 {'provider': 'astra', 'reason': '...'}
except TTSSessionError as e:
    print(e.code, e.details)   # TTS_004 {'session_id': '...', 'reason': '...'}
except TTSConfigError as e:
    print(e.code, e.details)   # TTS_005 {'param_name': '...', 'reason': '...'}
```

## 扩展新提供商

```python
from core.tts.providers.base import TTSProvider, TTSProviderFactory
from core.tts.models import TTSRequest
from typing import AsyncGenerator

class MyTTSProvider(TTSProvider):
    @property
    def provider_name(self) -> str:
        return "my_tts"

    async def create_session(self, request: TTSRequest) -> str:
        """创建合成会话，返回 session_id"""
        ...

    async def consume_session(self, session_id: str) -> AsyncGenerator[bytes, None]:
        """消费会话音频流，逐块 yield 音频数据"""
        ...

    async def close_session(self, session_id: str) -> None:
        """释放会话资源"""
        ...

    async def aclose(self) -> None:
        """释放提供商持有的资源（可选）"""
        ...

# 在 core/tts/__init__.py 中注册
TTSProviderFactory.register("my_tts", MyTTSProvider)
```

## 高级功能

### 播放控制

```python
# 暂停与恢复
player.pause()   # 暂停播放
player.resume()  # 恢复播放
player.stop()    # 停止播放并清空队列

# 音量控制
player.set_volume(0.5)  # 设置音量为 50%
volume = player.get_volume()  # 获取当前音量

# 播放进度
progress = player.get_progress()
print(f"已播放: {progress.bytes_played} 字节")
print(f"进度: {progress.progress_ratio:.1%}")
print(f"时长: {progress.duration_played:.2f}s")
```

### 进度回调

```python
def on_progress(progress: PlaybackProgress):
    print(f"播放进度: {progress.progress_ratio:.1%}")

player.set_progress_callback(on_progress)
```

### 性能调优

```python
# 调整预取队列大小（影响首字节延迟与内存占用）
service = create_service(
    "astra",
    {"api_url": "http://127.0.0.1:5000"},
    prefetch_queue_size=32,  # 增大队列，降低段间停顿
    max_concurrent_creates=20,  # 增大并发数，加快分段创建
)

# 调整播放队列大小（影响播放稳定性与内存占用）
player = create_player({
    "play_queue_size": 64,  # 增大队列，提高播放稳定性
    "frames_per_buffer": 512,  # 减小缓冲区，降低延迟
})
```
