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

echo -e "${GREEN}开始构建 AstraTTS 镜像...${NC}"

# 切换到项目根目录下的 AstraTTS 目录
cd "$PROJECT_ROOT/AstraTTS"

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
