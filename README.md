# CC-Switch Python 版

多 AI CLI 配置切换工具，支持一键切换不同的 AI 服务商配置（API Key、Base URL、模型），并提供图形界面和命令行两种操作方式。

## 功能特性

- **多服务商管理**：支持 OpenAI、Anthropic、Google Gemini、Claude Code、Codex、Hermes 等多种 AI CLI 工具的配置管理
- **一键切换**：在服务商配置之间快速切换，自动更新所有支持的 CLI 工具
- **HTTP 代理**：内置 OpenAI 兼容的 HTTP 代理服务，支持代理转发和虚拟模型路由
- **备份/回滚**：自动备份当前配置，支持随时回滚到历史备份
- **导入/导出**：JSON 格式的配置文件导入导出，方便迁移和分享
- **图形界面**：基于 CustomTkinter 的现代化 GUI，支持多种皮肤切换
- **命令行工具**：完整的 CLI 支持，适合脚本化和自动化操作
- **密钥管理**：安全的 API 密钥管理，配置文件自动加密存储

## 安装

### 环境要求

- Python 3.10+
- Windows / macOS / Linux

### 依赖安装

```bash
pip install customtkinter
```

## 使用方法

### 图形界面

```bash
python main.py gui
```

### 命令行

```bash
# 列出所有服务商配置
python main.py list

# 添加新的服务商配置
python main.py add <别名> <API密钥> <Base URL> <模型> [API格式]

# 切换到指定服务商
python main.py switch <别名>

# 启动 HTTP 代理服务
python main.py serve --host 127.0.0.1 --port 8787

# 查看当前状态
python main.py status

# 备份配置
python main.py backup

# 回滚到指定备份
python main.py rollback <备份目录名>

# 导入/导出配置
python main.py import <json文件>
python main.py export <json文件>

# 测试配置
python main.py test <别名> --proxy 127.0.0.1:8787
```

### 高级选项

```bash
# 指定切换的 CLI 工具
python main.py switch <别名> --clis claude_code,hermes

# 不创建备份
python main.py switch <别名> --no-backup

# 自定义代理地址
python main.py switch <别名> --proxy 127.0.0.1:8787

# 只配置 Hermes + 代理
python main.py hermes <别名> --proxy 127.0.0.1:8787
```

## 项目结构

```
pyswith/
├── main.py          # 程序入口，CLI 和 GUI 启动
├── core.py          # 核心逻辑：服务商管理、配置切换、代理服务器
├── gui.py           # 图形界面（CustomTkinter）
├── config/
│   ├── config.yaml      # 主配置文件
│   ├── skillopt_config.yaml  # SkillOpt 配置
│   └── token_usage.json     # Token 使用统计
├── .gitignore       # Git 忽略规则
└── README.md        # 项目说明
```

## 配置文件

### config.yaml

主配置文件，包含：
- 服务商列表及 API 密钥
- 代理服务器设置
- 默认服务商
- 支持的 CLI 工具配置

### skillopt_config.yaml

SkillOpt 配置，用于管理不同 AI CLI 工具的特定配置。

## 注意事项

- **不要提交 `.env` 文件**：API 密钥等敏感信息已添加到 `.gitignore`
- **定期备份**：切换配置前会自动创建备份
- **端口占用**：代理服务默认使用空闲端口，可通过 `--port` 指定

## 许可证

MIT License

## 作者

chleeken
