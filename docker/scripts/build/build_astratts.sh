#!/bin/bash
# ============================================================
# 构建 AstraTTS Docker 镜像
# ============================================================

set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 脚本所在目录 & 项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DOCKER_DATA="$PROJECT_ROOT/docker/data"

echo -e "${GREEN}开始构建 AstraTTS 镜像...${NC}"

# 切换到 docker/data/AstraTTS 目录（构建上下文）
cd "$DOCKER_DATA/AstraTTS"

# ── 准备核心资源 resources-minimal ──
# 上游已弃用 Git LFS，核心资源需从 GitHub Releases 单独下载。
# Dockerfile 中的 `COPY resources-minimal /app/resources` 依赖该目录，
# 缺失时构建会失败。此处自动下载并解压。
RESOURCE_ZIP="resources-minimal.zip"
RESOURCE_URL="https://github.com/Blackwood416/AstraTTS/releases/latest/download/${RESOURCE_ZIP}"
if [[ ! -d "resources-minimal" ]]; then
    echo -e "${YELLOW}未找到 resources-minimal，正在从 GitHub Releases 下载资源包...${NC}"
    if [[ ! -f "$RESOURCE_ZIP" ]]; then
        if ! curl -fL --retry 3 -o "$RESOURCE_ZIP" "$RESOURCE_URL"; then
            echo -e "${RED}✗ 资源包下载失败：$RESOURCE_URL${NC}"
            echo -e "${RED}  请手动下载并解压 resources-minimal.zip 到 $PWD${NC}"
            exit 1
        fi
    fi
    if ! unzip -q "$RESOURCE_ZIP"; then
        echo -e "${RED}✗ 资源包解压失败${NC}"
        exit 1
    fi
    rm -f "$RESOURCE_ZIP"
    echo -e "${GREEN}✓ 资源包下载并解压完成${NC}"
else
    echo -e "${GREEN}✓ resources-minimal 已存在${NC}"
fi

# 运行时 compose 通过 volume 挂载 docker/data/AstraTTS/resources:/app/resources，
# 需保证宿主 resources 目录存在，否则容器内 /app/resources 为空（Docker 自动创建空目录覆盖镜像）。
if [[ ! -d "resources" ]]; then
    echo -e "${YELLOW}未找到 resources，从 resources-minimal 复制作为运行时挂载源...${NC}"
    cp -r resources-minimal resources
    echo -e "${GREEN}✓ resources 已就绪${NC}"
fi

# compose 以 bind mount 挂载 config.yaml，宿主该文件必须存在。
if [[ ! -f "config.yaml" ]]; then
    cp config.template.yaml config.yaml
    echo -e "${GREEN}✓ config.yaml 已就绪（从 config.template.yaml 复制）${NC}"
fi

# 构建镜像
if docker build -t astratts-server:latest .; then
    echo -e "${GREEN}✓ AstraTTS 镜像构建成功！${NC}"
    echo -e "\033[0;36m现在可以运行: docker compose up -d${NC}"

    # ── 清理构建遗留的无用文件，只保留运行所需文件 ──
    KEEP=("resources" "config.template.yaml" "config.yaml" "README.md")
    for item in * .[!.]* ..?*; do
        # 跳过不存在的 glob
        [[ -e "$item" ]] || continue
        # 检查是否在保留列表中
        skip=false
        for k in "${KEEP[@]}"; do
            if [[ "$item" == "$k" ]]; then
                skip=true
                break
            fi
        done
        if [[ "$skip" == "false" ]]; then
            echo -e "${YELLOW}  清理 $item...${NC}"
            rm -rf "$item"
        fi
    done
    echo -e "${GREEN}✓ 已清理构建遗留文件，仅保留运行所需文件${NC}"
else
    echo -e "${RED}✗ 镜像构建失败，请检查错误信息${NC}"
    exit 1
fi
