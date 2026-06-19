# 构建 AstraTTS Docker 镜像

Write-Host "开始构建 AstraTTS 镜像..." -ForegroundColor Green

# 切换到 AstraTTS 目录
Set-Location AstraTTS

# 构建镜像
docker build -t astratts-server:latest .

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ AstraTTS 镜像构建成功！" -ForegroundColor Green
    Write-Host "现在可以运行: docker compose up -d" -ForegroundColor Cyan
} else {
    Write-Host "✗ 镜像构建失败，请检查错误信息" -ForegroundColor Red
    exit 1
}

# 返回根目录
Set-Location ..
