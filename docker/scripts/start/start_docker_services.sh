#!/bin/bash
# ============================================================
# Aliya-cosmos Docker 服务启动脚本
# ============================================================

set -e

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'

# 脚本所在目录 & 项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DOCKER_DIR="$PROJECT_ROOT/docker"
DOCKER_DATA="$DOCKER_DIR/data"

# ── 工具函数 ──
write_step() {
    echo -e "  ● $1"
}

write_ok() {
    echo -e "    ✔ $1"
}

write_fail() {
    echo -e "    ✘ $1"
}

test_image_exists() {
    local image_name="$1"
    docker images --format "{{.Repository}}:{{.Tag}}" 2>/dev/null | grep -q "^${image_name}$"
}

get_container_status() {
    local container_name="$1"
    docker inspect --format "{{.State.Status}}" "$container_name" 2>/dev/null || echo ""
}

start_service_container() {
    local container_name="$1"
    local status
    status=$(get_container_status "$container_name")

    if [[ "$status" == "running" ]]; then
        write_ok "$container_name 已在运行"
        return 0
    elif [[ "$status" == "exited" || "$status" == "created" || "$status" == "paused" ]]; then
        echo -e "      容器 $container_name 状态为 $status，正在启动..."
        if ! docker start "$container_name"; then
            write_fail "$container_name 启动失败"
            exit 1
        fi
        write_ok "$container_name 已启动"
        return 0
    fi
    return 1
}

# ── 标题 ──
echo ""
echo -e "${CYAN}  ╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}  ║   Aliya Cosmos Docker 服务启动       ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════╝${NC}"
echo ""

# 1. 检测 Docker
write_step "检测 Docker 运行状态..."
if ! docker info >/dev/null 2>&1; then
    write_fail "Docker 未运行，请先启动 Docker"
    exit 1
fi
write_ok "Docker 运行正常"

# 2. Neo4j
write_step "检测 Neo4j..."
if ! start_service_container "aliya-cosmos-neo4j"; then
    if test_image_exists "neo4j:latest"; then
        write_ok "neo4j:latest 已存在"
    else
        echo -e "      正在拉取 neo4j:latest..."
        if ! docker pull neo4j:latest; then
            write_fail "Neo4j 镜像拉取失败"
            exit 1
        fi
        write_ok "neo4j:latest 拉取完成"
    fi
fi

# 3. AstraTTS
write_step "检测 AstraTTS..."
if ! start_service_container "aliya-cosmos-astratts"; then
    if test_image_exists "astratts-server:latest"; then
        write_ok "astratts-server:latest 已存在"
    else
        echo -e "      准备构建 astratts-server:latest..."
        AstraTTSDir="$DOCKER_DATA/AstraTTS"
        if [[ ! -d "$AstraTTSDir" ]]; then
            echo -e "      正在克隆 AstraTTS 仓库..."
            if ! git clone https://github.com/Blackwood416/AstraTTS.git "$AstraTTSDir"; then
                write_fail "AstraTTS 仓库克隆失败"
                exit 1
            fi
            write_ok "AstraTTS 克隆完成"
        else
            write_ok "AstraTTS 目录已存在"
        fi
        cd "$AstraTTSDir"
        if ! docker build -t astratts-server:latest .; then
            write_fail "AstraTTS 镜像构建失败"
            exit 1
        fi
        cd "$PROJECT_ROOT"
        write_ok "astratts-server:latest 构建完成"
    fi
fi

# 4. Compose 启动
write_step "创建并启动容器..."
if ! docker compose -f "$DOCKER_DIR/compose/compose.yml" up -d; then
    write_fail "Compose 启动失败"
    exit 1
fi

# ── 完成 ──
echo ""
echo -e "${CYAN}  ╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║   所有服务已就绪                     ║${NC}"
echo -e "${CYAN}  ║                                      ║${NC}"
echo -e "${WHITE}  ║    Neo4j   : http://localhost:7474   ║${NC}"
echo -e "${WHITE}  ║    AstraTTS: http://localhost:5000   ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════╝${NC}"
echo ""
