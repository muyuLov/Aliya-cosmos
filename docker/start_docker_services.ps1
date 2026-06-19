# ============================================================
# Aliya-cosmos Docker 服务启动脚本
# ============================================================

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot

function Write-Step {
    param([string]$Text)
    Write-Host "  ● $Text" -ForegroundColor Cyan
}

function Write-OK {
    param([string]$Text)
    Write-Host "    ✔ $Text" -ForegroundColor Green
}

function Write-Error {
    param([string]$Text)
    Write-Host "    ✘ $Text" -ForegroundColor Red
}

# ── 标题 ──
Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   Aliya Cosmos Docker 服务启动       ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 工具函数 ──
function Test-ImageExists([string]$ImageName) {
    $list = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null
    return ($list -contains $ImageName)
}

function Get-ContainerStatus([string]$ContainerName) {
    $status = docker inspect --format "{{.State.Status}}" $ContainerName 2>$null
    return $status
}

function Start-ServiceContainer([string]$ContainerName, [string]$ServiceName) {
    $status = Get-ContainerStatus $ContainerName
    if ($status -eq "running") {
        Write-OK "$ContainerName 已在运行"
        return $true
    } elseif ($status -eq "exited" -or $status -eq "created" -or $status -eq "paused") {
        Write-Host "      容器 $ContainerName 状态为 $status，正在启动..." -ForegroundColor Yellow
        docker start $ContainerName
        if ($LASTEXITCODE -ne 0) {
            Write-Error "$ContainerName 启动失败"
            exit 1
        }
        Write-OK "$ContainerName 已启动"
        return $true
    }
    return $false
}

# 1. 检测 Docker
Write-Step "检测 Docker 运行状态..."
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker 未运行，请先启动 Docker Desktop"
    exit 1
}
Write-OK "Docker 运行正常"

# 2. Neo4j
Write-Step "检测 Neo4j..."
$neo4jDone = Start-ServiceContainer "aliya-cosmos-neo4j" "neo4j"
if (-not $neo4jDone) {
    if (Test-ImageExists "neo4j:latest") {
        Write-OK "neo4j:latest 已存在"
    } else {
        Write-Host "      正在拉取 neo4j:latest..." -ForegroundColor Yellow
        docker pull neo4j:latest
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Neo4j 镜像拉取失败"
            exit 1
        }
        Write-OK "neo4j:latest 拉取完成"
    }
}

# 3. AstraTTS
Write-Step "检测 AstraTTS..."
$astraDone = Start-ServiceContainer "aliya-cosmos-astratts" "astratts"
if (-not $astraDone) {
    if (Test-ImageExists "astratts-server:latest") {
        Write-OK "astratts-server:latest 已存在"
    } else {
        Write-Host "      准备构建 astratts-server:latest..." -ForegroundColor Yellow
        $AstraTTSDir = Join-Path $ProjectRoot "AstraTTS"
        if (-not (Test-Path $AstraTTSDir)) {
            Write-Host "      正在克隆 AstraTTS 仓库..." -ForegroundColor Yellow
            git clone https://github.com/Blackwood416/AstraTTS.git "$AstraTTSDir"
            if ($LASTEXITCODE -ne 0) {
                Write-Error "AstraTTS 仓库克隆失败"
                exit 1
            }
            Write-OK "AstraTTS 克隆完成"
        } else {
            Write-OK "AstraTTS 目录已存在"
        }
        Push-Location $AstraTTSDir
        docker build -t astratts-server:latest .
        $buildCode = $LASTEXITCODE
        Pop-Location
        if ($buildCode -ne 0) {
            Write-Error "AstraTTS 镜像构建失败"
            exit 1
        }
        Write-OK "astratts-server:latest 构建完成"
    }
}

# 4. Compose 启动
Write-Step "创建并启动容器..."
docker compose -f "$ProjectRoot\compose.yml" up -d
if ($LASTEXITCODE -ne 0) {
    Write-Error "Compose 启动失败"
    exit 1
}

# ── 完成 ──
Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   所有服务已就绪                      ║" -ForegroundColor Green
Write-Host "  ║                                      ║" -ForegroundColor Cyan
Write-Host "  ║    Neo4j  : http://localhost:7474    ║" -ForegroundColor White
Write-Host "  ║    AstraTTS: http://localhost:5000   ║" -ForegroundColor White
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
