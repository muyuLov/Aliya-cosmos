# PyProject.toml 优化指南

本文档说明了 Aliya-cosmos 项目中 `pyproject.toml` 文件的优化改进。

## 🎯 优化内容

### 1. **项目元数据规范化**
- ✅ 统一项目名称为小写 `aliya-cosmos`
- ✅ 完善项目描述和关键词
- ✅ 添加更多分类标签
- ✅ 规范化URL格式
- ✅ 添加构建系统配置

### 2. **依赖管理优化**
- ✅ **清晰分组**：按功能模块对依赖进行分组
- ✅ **版本约束**：为所有依赖添加合理的版本上下限
- ✅ **重复清理**：移除重复的FFmpeg相关依赖
- ✅ **优先级排序**：核心依赖优先，可选依赖分类

### 3. **开发工具配置增强**
- ✅ **测试工具**：pytest + coverage + 异步支持
- ✅ **代码质量**：black + isort + flake8 + mypy
- ✅ **文档生成**：mkdocs + material主题
- ✅ **性能分析**：py-spy + memory-profiler + line-profiler

### 4. **工具配置完善**
- ✅ **Black**：代码格式化配置
- ✅ **isort**：导入语句排序
- ✅ **MyPy**：类型检查（逐步启用严格模式）
- ✅ **Pytest**：测试框架配置 + 覆盖率
- ✅ **Coverage**：代码覆盖率报告
- ✅ **Flake8**：代码风格检查

## 📦 依赖分组说明

### 核心依赖 (18个)
```toml
dependencies = [
    # 核心框架
    "pydantic", "pyyaml"
    
    # 网络HTTP客户端  
    "httpx", "requests", "urllib3"
    
    # LLM大语言模型
    "openai"
    
    # 图数据库记忆系统
    "py2neo"
    
    # TTS语音合成
    "numpy", "sounddevice", "edge-tts"
    "portable-ffmpeg", "imageio-ffmpeg"
    
    # Web服务框架
    "fastapi", "uvicorn"
    
    # 系统监控
    "psutil"
    
    # GUI桌面客户端
    "pywebview", "pystray", "Pillow"
]
```

### 可选依赖组

| 组名 | 用途 | 包含工具 |
|------|------|----------|
| `dev` | 开发测试 | pytest, pytest-asyncio, pytest-cov, coverage |
| `lint` | 代码质量 | black, isort, flake8, mypy |
| `pre-commit` | Git钩子 | pre-commit |
| `docs` | 文档生成 | mkdocs, mkdocs-material |
| `profile` | 性能分析 | py-spy, memory-profiler, line-profiler |
| `all` | 完整环境 | 包含上述所有工具 |

## 🚀 使用方法

### 基础安装
```bash
# 安装核心依赖
uv sync

# 或使用pip
pip install -e .
```

### 开发环境
```bash
# 安装开发依赖
uv sync --extra dev

# 安装完整开发环境
uv sync --extra all

# 只安装特定工具组
uv sync --extra lint --extra docs
```

### 代码质量检查
```bash
# 代码格式化
black .
isort .

# 类型检查
mypy core/ agent/

# 风格检查  
flake8

# 运行测试
pytest

# 生成覆盖率报告
pytest --cov=core --cov=agent --cov-report=html
```

### 性能分析
```bash
# 安装性能分析工具
uv sync --extra profile

# CPU性能分析
py-spy record -o profile.svg -- python your_script.py

# 内存使用分析
mprof run your_script.py
mprof plot
```

## 🔧 配置特点

### 逐步严格类型检查
```toml
# 对特定模块启用严格类型检查
[[tool.mypy.overrides]]
module = [
    "core.config.*",
    "core.logger.*", 
    "agent.tools.*",
]
strict = true
```

### 智能测试标记
```toml
markers = [
    "slow: 标记运行缓慢的测试",
    "integration: 标记集成测试", 
    "unit: 标记单元测试",
    "tts: 标记TTS相关测试",
    "memory: 标记记忆系统测试",
    "llm: 标记LLM相关测试",
]
```

### 覆盖率排除规则
```toml
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
    "@(abc\\.)?abstractmethod",
]
```

## ✨ 优化效果

- 🎯 **依赖清晰**：按功能分组，易于理解和维护
- 🛡️ **版本安全**：合理的版本约束防止兼容性问题  
- 🔧 **工具齐全**：涵盖开发、测试、文档、分析全流程
- 📊 **质量保证**：多层次的代码质量检查
- 🚀 **开发效率**：一键安装所需的开发工具
- 📈 **可扩展性**：结构化的配置便于后续扩展

## 🔄 迁移指南

如果从旧的pyproject.toml迁移，建议：

1. **备份当前配置**
2. **逐步更新依赖**：先更新核心依赖，再添加可选依赖
3. **运行验证脚本**：`python validate_project.py`
4. **测试关键功能**：确保TTS、记忆系统等核心功能正常

---

*此配置遵循Python包装标准和最佳实践，为项目长期发展提供坚实基础。*