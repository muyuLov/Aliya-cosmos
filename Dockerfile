# syntax=docker/dockerfile:1.7
# =====================================================================
# Aliya-cosmos — 生产级多阶段构建
#   - builder: 用 uv 在独立 venv 中解析并安装全部依赖
#   - runtime: 仅含运行时必需的最小系统库 + 应用代码（非 root 运行）
# 说明：依赖版本由仓库内的 uv.lock 锁定（uv sync --frozen），
#       构建可复现；基础镜像与 uv 版本通过 ARG 固定。
# =====================================================================

# ---------------------------------------------------------------------
# Stage 1: builder — 构建依赖 venv
# ---------------------------------------------------------------------
FROM python:3.13-slim AS builder

# 构建期系统依赖：仅保留编译原生扩展所需（build-essential）。
# python:3.13-slim 已自带 ca-certificates；锁文件构建无需 git。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 固定 uv 版本，避免构建行为漂移
ARG UV_VERSION=0.7.13
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

# 让 uv 把虚拟环境直接装到 /opt/venv，并把字节码写入产物以加速启动
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 仅复制依赖解析所需文件，最大化利用层缓存：
# 只有 pyproject.toml / uv.lock 变化才会触发下面的依赖安装层。
COPY pyproject.toml uv.lock ./

# 第一步：只安装三方依赖（不安装本项目），该层与源码解耦。
# BuildKit 缓存挂载加速重复构建，且不污染镜像层。
# 无锁文件（如 CI 未提交 uv.lock）时回退到在线解析。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    || uv sync --no-dev --no-install-project

# 复制包源码（setuptools 需要按 pyproject 的 packages 构建项目）
COPY agent ./agent
COPY core ./core
COPY GUI ./GUI
COPY main.py README.md LICENSE.txt ./

# 第二步：安装本项目（editable），依赖已就绪，此层极快且仅随源码变化失效。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# ---------------------------------------------------------------------
# Stage 2: runtime — 最小运行镜像
# ---------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# 运行时必需的系统库：
#   libportaudio2  -> sounddevice（TTS 播放器顶层导入，缺此库进程起不来）
#   ffmpeg         -> portable-ffmpeg / imageio-ffmpeg 的兜底后端
#   tini           -> 作为 PID 1 正确转发信号、回收僵尸进程
RUN apt-get update \
    && apt-get install -y --no-install-recommends libportaudio2 ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户（UID/GID 固定，便于挂载卷时权限对齐）
RUN groupadd -r -g 1001 aliya \
    && useradd -r -m -u 1001 -g aliya -d /home/aliya aliya

# 从 builder 阶段复制已装好的虚拟环境（路径保持一致 /opt/venv）
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# 复制应用代码（受 .dockerignore 约束，已排除 .git/.venv/大型数据等）
COPY --chown=aliya:aliya . /app

# 运行时环境变量（均可在 docker-compose 中被覆盖）
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1

# 应用相关默认值（容器内环境覆盖；与 docker-compose environment 保持一致）
ENV WS_HOST=0.0.0.0 \
    WS_PORT=8765 \
    NEO4J_HOST=neo4j \
    NEO4J_PORT=7687 \
    NEO4J_PASSWORD=Aliya_neo4j

# 应用运行时可写目录（日志、TTS 缓存），归属非 root 用户
RUN mkdir -p /app/data/logs /app/data/cache /tmp/aliya \
    && chown -R aliya:aliya /app/data/logs /app/data/cache /tmp/aliya

USER aliya

EXPOSE 8765

# 健康检查：WebSocket 服务无 HTTP 端点，使用 TCP 端口探活。
# 端口取自 WS_PORT 环境变量，uvicorn 监听即视为存活。
HEALTHCHECK --interval=30s --timeout=5s --start_period=20s --retries=3 \
  CMD python -c "import os,socket,sys; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', int(os.environ.get('WS_PORT','8765')))); s.close()"

# tini 作为 PID 1，确保 SIGTERM 等信号被正确转发给 Python 进程
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]
