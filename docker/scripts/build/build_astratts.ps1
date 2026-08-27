# 构建 AstraTTS Docker 镜像

# 脚本所在目录 & 项目根目录
$ScriptDir = $PSScriptRoot
$ProjectRoot = (Get-Item $ScriptDir).Parent.Parent.Parent.FullName

Write-Host "开始构建 AstraTTS 镜像..." -ForegroundColor Green

# 切换到项目根目录下的 AstraTTS 目录
Set-Location "$ProjectRoot\AstraTTS"

# 构建镜像
docker build -t astratts-server:latest .

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ AstraTTS 镜像构建成功！" -ForegroundColor Green
    Write-Host "现在可以运行: docker compose up -d" -ForegroundColor Cyan

    # ── 清理构建遗留的无用文件，只保留运行所需文件 ──
    $Keep = @("resources", "config.template.yaml", "config.yaml", "README.md")
    Get-ChildItem -Force | Where-Object { $Keep -notcontains $_.Name } | ForEach-Object {
        Write-Host "  清理 $($_.Name)..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $_.FullName
    }
    Write-Host "✓ 已清理构建遗留文件，仅保留运行所需文件" -ForegroundColor Green
} else {
    Write-Host "✗ 镜像构建失败，请检查错误信息" -ForegroundColor Red
    exit 1
}
