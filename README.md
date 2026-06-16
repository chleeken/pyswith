# CC-Switch Python

> **多 AI CLI 配置切换 + OpenAI 兼容 API 代理**  
> Python 3.10+ / CustomTkinter / 纯标准库 HTTP Server  
> 一个程序搞定：多服务商 Key 管理 → 一键切换给多个 AI CLI → 本地 OpenAI 兼容 API 网关（含虚拟模型）

---

## ✨ 功能

| 模块 | 说明 |
|------|------|
| 🗝️ **服务商管理** | 添加/编辑/删除 OpenAI 兼容服务商（DeepSeek、NVIDIA、OpenRouter、硅基流动、Anthropic、Gemini…） |
| 📋 **模型配置** | 每个服务商独立的 API Key、模型名、BaseURL、API 格式（openai / anthropic / google） |
| 🚀 **一键切换** | 选中服务商，自动写入 **Claude Code / OpenAI Codex / Gemini CLI / Hermes Agent** 配置 |
| 🌐 **API 代理** | 本地启动 OpenAI 兼容代理（默认 `http://127.0.0.1:3000/v1`），支持 `/v1/chat/completions`、`/v1/models`，含**虚拟模型 ID** 映射 + **流式响应** |
| 🎭 **虚拟模型** | 暴露一个固定的虚拟模型（默认 `virtual-model`），代理动态映射到真实服务商，外部 Agent 永远用同一个模型 ID |
| 📊 **Token 用量统计** | 跟踪每日/每月 token 用量，自动日/月切换清零，持久化到 `config/token_usage.json` |
| 📁 **备份/回滚** | 切换前自动快照；一键恢复到任意时间点 |
| 🧪 **可用性测试** | 直连上游发一次 ping，返回状态码 + 耗时 |
| 💬 **Hermes 集成** | 完整 Hermes Agent 适配：`custom_providers`、`model.default`、`model.provider` 统一走代理 |
| 🎨 **皮肤系统** | 8 套皮肤（粉灰/蓝白/粉白/白绿/蓝灰/粉乳/乳绿/暗黑）+ 字体/字号/颜色自定义 |
| 🔄 **导入/导出** | JSON 格式批量迁移服务商配置 |

---

## 🚀 快速开始

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/chleeken/cc-switch-python.git
cd cc-switch-python

# 2. 安装依赖（仅 GUI 需要）
pip install customtkinter>=5.2.2
```

### 配置

编辑 `config/config.yaml`：

```yaml
providers:
  deepseek-chat:
    display_name: deepseek-chat
    base_url: https://api.deepseek.com/v1
    api_key: sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
    model: deepseek-chat
    api_format: openai

proxy:
  host: 127.0.0.1
  port: 3000
  virtual_model: virtual-model
```

> ⚠️ `config/config.yaml` 含敏感 API Key，已配置在 `.gitignore` 中，**不会被提交到仓库**。参考格式见 `config/config.example.yaml`。

---

## 💻 使用方式

### 图形界面

```bash
python main.py
```

### 命令行

```bash
# 列出服务商
python main.py list

# 添加服务商
python main.py add <alias> <api_key> <base_url> <model> [api_format]

# 删除服务商
python main.py remove <alias>

# 切换服务商（写入所有 CLI 配置）
python main.py switch <alias>

# 切换并启动代理
python main.py switch <alias> --proxy 127.0.0.1:8787

# 查看当前状态
python main.py status

# 备份
python main.py backup

# 回滚
python main.py rollback <backup_dir_name>

# 导入/导出
python main.py export config.json
python main.py import config.json [--merge]

# 启动 HTTP 代理
python main.py serve

# 冒烟测试
python main.py test <alias> --proxy 127.0.0.1:8787
```

---

## 🏗️ 项目结构

```
ccswith2python/
├── main.py          # 程序入口（GUI + CLI）
├── core.py          # 核心逻辑：服务商管理、CLI 配置、代理、备份
├── gui.py           # CustomTkinter 桌面图形界面
├── fix_cli_proxy.py # CLI 代理修复工具
├── config/
│   ├── config.yaml  # 服务商配置（gitignore，不提交）
│   └── token_usage.json  # 用量统计（gitignore，不提交）
├── config_backups/  # 备份快照（gitignore，不提交）
├── icon.ico         # 窗口图标
└── requirements.txt # Python 依赖
```

---

## 🔧 支持的 CLI

| CLI | 配置位置 | 说明 |
|-----|----------|------|
| **Claude Code** | `~/.claude/settings.json` | Anthropic API + BaseURL |
| **OpenAI Codex** | `~/.codex/config.json` | OpenAI 格式 Key + 模型 |
| **Gemini CLI** | `~/.gemini/settings.yaml` | Google AI 格式 |
| **Hermes Agent** | `~/.hermes/config.yaml` | custom_providers + proxy |

---

## 📜 许可

MIT License
