# TTS 模块配置指南

## 概述

TTS 模块的关键参数现在支持通过构造函数配置，适应不同性能需求的 TTS 服务。

## 常量定义

所有常量统一定义在 `core.tts.constants` 模块中：

```python
from core.tts.constants import (
    SENTINEL,                          # 哨兵对象
    DEFAULT_PREFETCH_QUEUE_SIZE,       # 16
    DEFAULT_MAX_CONCURRENT_CREATES,    # 10
    DEFAULT_PLAY_QUEUE_SIZE,           # 32
    DEFAULT_FRAMES_PER_BUFFER,         # 1024
    WAV_DETECT_SIZE,                   # 512
)
```

## TTSService 配置

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | `TTSProvider` | 必需 | TTS 提供商实例 |
| `voice_config` | `VoiceConfig \| None` | `None` | 音色默认配置 |
| `prefetch_queue_size` | `int` | `16` | 预取队列大小，控制并发预取的段数 |
| `max_concurrent_creates` | `int` | `10` | 最大并发创建会话数 |

### 使用示例

#### 1. 默认配置（适合大多数场景）

```python
from core.tts import TTSService
from core.tts.providers import AstraTTSProvider

provider = AstraTTSProvider(base_url="http://localhost:5000")
service = TTSService(provider=provider)
```

#### 2. 高性能 TTS 服务（增加并发）

```python
# 适用于响应快、并发能力强的 TTS 服务
service = TTSService(
    provider=provider,
    prefetch_queue_size=32,        # 增加预取队列，降低延迟
    max_concurrent_creates=20,     # 增加并发创建数
)
```

#### 3. 低性能 TTS 服务（减少并发）

```python
# 适用于响应慢、资源受限的 TTS 服务
service = TTSService(
    provider=provider,
    prefetch_queue_size=8,         # 减少预取队列，降低内存占用
    max_concurrent_creates=3,      # 减少并发创建数，避免过载
)
```

#### 4. 低延迟优先（激进预取）

```python
# 牺牲内存换取更低的首字节延迟
service = TTSService(
    provider=provider,
    prefetch_queue_size=64,        # 大幅增加预取
    max_concurrent_creates=30,     # 高并发
)
```

### 参数调优建议

#### prefetch_queue_size

- **作用**：控制同时预取的文本段数量
- **影响**：
  - 增大：降低段间停顿，但增加内存占用和初始延迟
  - 减小：降低内存占用，但可能增加段间停顿
- **推荐值**：
  - 快速 TTS（< 100ms/段）：32-64
  - 中速 TTS（100-500ms/段）：16（默认）
  - 慢速 TTS（> 500ms/段）：4-8

#### max_concurrent_creates

- **作用**：限制同时创建的 TTS 会话数量
- **影响**：
  - 增大：提高并发处理能力，但可能过载 TTS 服务
  - 减小：保护 TTS 服务，但可能增加排队延迟
- **推荐值**：
  - 本地 TTS 服务：20-30
  - 远程 TTS 服务：10（默认）
  - 资源受限服务：3-5

## AudioPlayer 配置

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sample_rate` | `int` | `32000` | PCM 模式采样率（Hz） |
| `channels` | `int` | `1` | PCM 模式声道数 |
| `pcm_format` | `PcmFormat` | `"float32"` | PCM 格式 |
| `frames_per_buffer` | `int` | `1024` | pyaudio 每次写入帧数 |
| `play_queue_size` | `int` | `32` | 播放队列大小 |

### 使用示例

#### 1. 默认配置

```python
from core.tts import AudioPlayer

player = AudioPlayer()
```

#### 2. 低延迟配置

```python
# 适用于实时对话场景
player = AudioPlayer(
    frames_per_buffer=512,    # 减小缓冲区，降低延迟
    play_queue_size=16,       # 减小队列，降低延迟
)
```

#### 3. 高稳定性配置

```python
# 适用于网络不稳定或高负载场景
player = AudioPlayer(
    frames_per_buffer=2048,   # 增大缓冲区，提高稳定性
    play_queue_size=64,       # 增大队列，容忍更多抖动
)
```

#### 4. 高质量音频配置

```python
# 适用于高采样率、多声道音频
player = AudioPlayer(
    sample_rate=48000,        # 高采样率
    channels=2,               # 立体声
    pcm_format="int16",       # CD 质量
    frames_per_buffer=2048,
)
```

### 参数调优建议

#### frames_per_buffer

- **作用**：控制 pyaudio 每次写入的帧数
- **影响**：
  - 增大：提高稳定性，但增加延迟
  - 减小：降低延迟，但可能出现卡顿
- **推荐值**：
  - 低延迟场景：512
  - 平衡场景：1024（默认）
  - 高稳定性场景：2048-4096

#### play_queue_size

- **作用**：控制播放队列的容量
- **影响**：
  - 增大：容忍更多网络抖动，但增加内存占用
  - 减小：降低内存占用和延迟，但对抖动敏感
- **推荐值**：
  - 低延迟场景：16
  - 平衡场景：32（默认）
  - 高抖动场景：64-128

## 完整配置示例

### 场景 1：实时对话系统

```python
from core.tts import TTSService, AudioPlayer
from core.tts.providers import AstraTTSProvider

# 低延迟 TTS 服务
provider = AstraTTSProvider(base_url="http://localhost:5000")
service = TTSService(
    provider=provider,
    prefetch_queue_size=8,
    max_concurrent_creates=5,
)

# 低延迟播放器
player = AudioPlayer(
    frames_per_buffer=512,
    play_queue_size=16,
)
```

### 场景 2：高质量内容生成

```python
# 高并发 TTS 服务
service = TTSService(
    provider=provider,
    prefetch_queue_size=32,
    max_concurrent_creates=20,
)

# 高质量播放器
player = AudioPlayer(
    sample_rate=48000,
    channels=2,
    pcm_format="int16",
    frames_per_buffer=2048,
    play_queue_size=64,
)
```

### 场景 3：资源受限环境

```python
# 低资源 TTS 服务
service = TTSService(
    provider=provider,
    prefetch_queue_size=4,
    max_concurrent_creates=2,
)

# 低资源播放器
player = AudioPlayer(
    frames_per_buffer=1024,
    play_queue_size=16,
)
```

## 性能监控

### 关键指标

1. **TTFB（Time To First Byte）**：首字节延迟
   - 受 `prefetch_queue_size` 和 `max_concurrent_creates` 影响
   - 目标：< 500ms

2. **段间停顿**：相邻音频段之间的静音
   - 受 `prefetch_queue_size` 影响
   - 目标：< 100ms

3. **播放卡顿**：音频播放中断
   - 受 `frames_per_buffer` 和 `play_queue_size` 影响
   - 目标：0 次

4. **内存占用**：队列缓冲区占用的内存
   - 受所有队列大小参数影响
   - 目标：< 100MB

### 调优流程

1. **基线测试**：使用默认配置测试性能
2. **识别瓶颈**：分析 TTFB、停顿、卡顿等指标
3. **调整参数**：根据瓶颈调整相应参数
4. **验证效果**：重新测试并对比指标
5. **迭代优化**：重复 2-4 步直到满足需求

## 注意事项

1. **参数相互影响**：增大队列大小会增加延迟和内存占用
2. **TTS 服务能力**：参数应匹配 TTS 服务的实际性能
3. **系统资源**：确保有足够的 CPU 和内存支持配置
4. **网络条件**：不稳定网络需要更大的缓冲区
5. **用户体验**：平衡延迟、稳定性和资源占用
