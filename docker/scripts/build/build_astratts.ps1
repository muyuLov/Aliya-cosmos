# 构建 AstraTTS Docker 镜像

# 脚本所在目录 & 项目根目录
$ScriptDir = $PSScriptRoot
$ProjectRoot = (Get-Item $ScriptDir).Parent.Parent.Parent.FullName
$DockerData = Join-Path $ProjectRoot "docker\data"

Write-Host "开始构建 AstraTTS 镜像..." -ForegroundColor Green

# 切换到 docker/data/AstraTTS 目录（构建上下文）
Set-Location "$DockerData\AstraTTS"

# ── 准备核心资源 resources-minimal ──
# 上游已弃用 Git LFS，核心资源需从 GitHub Releases 单独下载。
# Dockerfile 中的 `COPY resources-minimal /app/resources` 依赖该目录，
# 缺失时构建会失败。此处自动下载并解压。
$ResourceZip = "resources-minimal.zip"
$ResourceUrl = "https://github.com/Blackwood416/AstraTTS/releases/latest/download/$ResourceZip"
if (-not (Test-Path "resources-minimal")) {
    Write-Host "未找到 resources-minimal，正在从 GitHub Releases 下载资源包..." -ForegroundColor Yellow
    if (-not (Test-Path $ResourceZip)) {
        Invoke-WebRequest -Uri $ResourceUrl -OutFile $ResourceZip
        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ 资源包下载失败：$ResourceUrl" -ForegroundColor Red
            Write-Host "  请手动下载并解压 resources-minimal.zip 到 $PWD" -ForegroundColor Red
            exit 1
        }
    }
    Expand-Archive -Path $ResourceZip -DestinationPath . -Force
    Remove-Item $ResourceZip -Force
    Write-Host "✓ 资源包下载并解压完成" -ForegroundColor Green
} else {
    Write-Host "✓ resources-minimal 已存在" -ForegroundColor Green
}

# 运行时 compose 通过 volume 挂载 docker/data/AstraTTS/resources:/app/resources，
# 需保证宿主 resources 目录存在，否则容器内 /app/resources 为空（Docker 自动创建空目录覆盖镜像）。
if (-not (Test-Path "resources")) {
    Write-Host "未找到 resources，从 resources-minimal 复制作为运行时挂载源..." -ForegroundColor Yellow
    Copy-Item -Path "resources-minimal" -Destination "resources" -Recurse -Force
    Write-Host "✓ resources 已就绪" -ForegroundColor Green
}

# compose 以 bind mount 挂载 config.yaml，宿主该文件必须存在。
if (-not (Test-Path "config.yaml")) {
    Copy-Item -Path "config.template.yaml" -Destination "config.yaml" -Force
    Write-Host "✓ config.yaml 已就绪（从 config.template.yaml 复制）" -ForegroundColor Green
}

# 构建镜像
docker build -t astratts-server:latest .
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ 镜像构建失败，请检查错误信息" -ForegroundColor Red
    exit 1
}

Write-Host "✓ AstraTTS 镜像构建成功！" -ForegroundColor Green
Write-Host "现在可以运行: docker compose up -d" -ForegroundColor Cyan

# ── 清理构建遗留的无用文件，只保留运行所需文件 ──
$Keep = @("resources", "config.template.yaml", "config.yaml", "README.md")
Get-ChildItem -Force | Where-Object { $Keep -notcontains $_.Name } | ForEach-Object {
    Write-Host "  清理 $($_.Name)..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $_.FullName
}
Write-Host "✓ 已清理构建遗留文件，仅保留运行所需文件" -ForegroundColor Green
