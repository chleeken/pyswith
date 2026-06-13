# CC-Switch — 多 AI CLI 配置切换工具 + OpenAI 兼容 API 代理

> Python 3.10+ / CustomTkinter / 纯标准库 HTTP 代理  
> 一个程序搞定：**多服务商 key 管理 + 一键切换给多个 AI CLI + 本地 OpenAI 兼容 API 网关（含虚拟模型）**

---

## ✨ 功能概览

| 模块 | 说明 |
| --- | --- |
| 🗝️ 服务商管理 | 添加 / 编辑 / 删除 OpenAI 兼容服务商（DeepSeek、NVIDIA、OpenRouter、硅基流动、Anthropic、Gemini…） |
| 📋 模型 / BaseURL / Key | 每个服务商独立的 API Key、模型名、BaseURL、API 格式（openai / anthropic / google） |
| 🚀 一键切换 | 选中服务商，自动写入 **Claude Code / OpenAI Codex / Gemini CLI / Hermes Agent** 的配置 |
| 🌐 API 代理 | 本地起 OpenAI 兼容代理（默认 `http://127.0.0.1:3000/v1`），支持 `/v1/chat/completions`、`/v1/models`，含**虚拟模型 ID** 映射，支持流式响应 `stream=true` |
| 📦 虚拟模型 | 暴露一个固定的虚拟模型（默认 `virtual-model`），代理动态映射到真实服务商，外部 Agent 永远用同一个模型 ID |
| 📁 备份 / 回滚 | 切换前自动快照；一键恢复到任意快照 |
| 🧪 可用性测试 | 每个服务商右侧 ✅ 测试按钮：**直连上游**（不走代理）发一次 ping，返回状态码 + 耗时 |
| 💬 Hermes 专用 | 完整 Hermes Agent 集成：`custom_providers`、`model.default`、`model.provider` 统一走代理 |
| 🎨 皮肤系统 | 8 套皮肤（粉灰 / 蓝白 / 粉白 / 白绿 / 蓝灰 / 粉乳 / 乳绿 / 暗黑）+ 字体 / 字号 / 颜色自定义 |
| 🪵 日志 / 进度 | 右侧实时日志框（清空 / 复制按钮）；左下角流式进度条；悬浮消息自动消失 |

---

## 📦 支持的目标 CLI

| CLI | 配置写入位置 |
| --- | --- |
| Claude Code | `~/.claude/.env`、`~/.claude/settings.json` |
| OpenAI Codex | `~/.codex/config.toml`、`~/.codex/config.json` |
| Gemini CLI | `~/.gemini/settings.json`、`~/.gemini/.env` |
| Hermes Agent | `~/.hermes/config.yaml` / `HERMES_HOME/config.yaml` / `AppData/Local/hermes/config.yaml`、`.env` |

> 目标 CLI 的勾选状态保存到 `config.yaml`，下次打开自动恢复。

---

## 🚀 快速开始

```powershell
# 1. 安装依赖（仅 CustomTkinter）
pip install -r requirements.txt

# 2. 启动 GUI（自动启动 API 代理到 3000）
python main.py

# 3. 命令行（可选）
python main.py list                           # 列出所有服务商
python main.py add nvidia YOUR_KEY integrate.api.nvidia.com deepseek-ai/deepseek-v4-flash
python main.py switch nvidia                  # 写入所有 CLI
python main.py serve --host 127.0.0.1 --port 3000   # 单独起代理
```

---

## 🌐 API 代理

### 默认参数
```
Host:   127.0.0.1
Port:   3000
Base:   http://127.0.0.1:3000/v1
虚拟模型: virtual-model
```

### 支持的端点

| 方法 | URL | 说明 |
| --- | --- | --- |
| POST | `/v1/chat/completions` | 核心聊天补全，支持 `stream=true` 流式返回 |
| POST | `/v1/responses` | OpenAI Responses API（会转换为 chat.completions） |
| GET  | `/v1/models` | 返回虚拟模型 + 当前真实模型 |
| GET  | `/health` | 健康检查 |
| *    | `/{any}` | 回退：直传到上游服务商 |

### 服务商适配

- OpenAI 兼容服务商 → 直接透传
- Anthropic Claude → 自动转 `messages` 协议为 Anthropic 格式（`/v1/messages`）
- Google Gemini → 自动转 `gemini/v1beta/models/...:generateContent`
- NVIDIA / OpenRouter / DeepSeek / 硅基流动 等 → 全部走 OpenAI 兼容协议

### 测试 curl

```bash
curl http://127.0.0.1:3000/v1/chat/completions \
  -H "Authorization: Bearer ANY-KEY-WORKS" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "virtual-model",
    "messages": [{"role":"user","content":"你好"}]
  }'
```

> 代理的 Authorization 头**不做校验**，只转发给真实上游；真正的 key 由 CC-Switch 在服务商配置里保存。

---

## 🧪 服务商测试按钮

每个服务商右侧有绿色 ✅ 按钮（GUI）：

```
直接 POST 上游 /v1/chat/completions
payload = {"model": 真实模型, "messages": [{"role":"user","content":"ping"}]}
Authorization: 服务商真实 key
超时: 30s
```

日志里会显示：

```
✅ 服务商 NVIDIA 可用!  upstream=https://integrate.api.nvidia.com/v1/chat/completions  status=200  耗时=1245ms
❌ 服务商 DeepSeek 失败 HTTP 401 upstream=https://api.deepseek.com/v1/chat/completions  耗时=312ms  body={"error":{"message":"Incorrect API key"}}
```

悬浮提示 1 秒后自动消失。

---

## 📋 服务商添加（GUI 或 CLI）

### GUI：左侧➕新增 → 填信息 → 💾保存
字段：
- 名称（别名，比如 `nvidia`）
- 显示名（比如 `NVIDIA`）
- API Key
- BaseURL（比如 `https://integrate.api.nvidia.com/v1`）
- 模型名（比如 `deepseek-ai/deepseek-v4-flash`）
- API 格式：`openai` / `anthropic` / `google`

### CLI：
```powershell
python main.py add deepseek sk-xxxxx https://api.deepseek.com v3
python main.py add nvidia sk-xxxxx integrate.api.nvidia.com deepseek-ai/deepseek-v4-flash
python main.py add claude sk-ant-xxx https://api.anthropic.com claude-sonnet-4.6 --format anthropic
python main.py add gemini AIzaSy... https://generativelanguage.googleapis.com gemini-2.0-flash --format google
python main.py remove deepseek
```

---

## 📂 项目结构

```json
ccswith/
├── main.py                   # 入口（CLI + GUI 启动）
├── core.py                   # 核心：路径/服务商/CLI适配/备份/YAML/HTTP代理
├── gui.py                    # CustomTkinter 界面
├── fix_cli_proxy.py          # 一键把 Codex + Hermes 都指向代理 3000
├── reset_codex.py            # 重置 Codex 原始配置
├── test_proxy_endpoints.py   # 验证代理到上游的脚本
├── requirements.txt          # 依赖（仅 customtkinter）
├── icon.ico                  # 程序图标
├── LICENSE                   # MIT 许可
├── .gitignore
└── config/
    ├── config.example.yaml   # 配置模板（无密钥，可直接复制使用）
    └── config.yaml           # 实际配置（含 API Key，已被 .gitignore 排除）
```

---

## ⚙️ 配置文件 `config.yaml`

程序目录下（PyInstaller / Nuitka 打包后仍能正确定位）。

|```yaml
# 完整配置示例见 config/config.example.yaml
# 正式使用时复制为 config/config.yaml 并填入自己的 API Key
providers:
  example-provider:
    display_name: Example
    base_url: https://api.example.com/v1
    api_key: YOUR_API_KEY_HERE
    model: your-model
    api_format: openai
    enabled: true

current_provider: example-provider

proxy:
  host: 127.0.0.1
  port: 3000
  virtual_model: virtual-model

ui_settings:
  enabled_clis: [claude_code, codex, gemini, hermes]
```

---

## 🔧 常见问题

### Hermes 报错 `HTTP 404: 404 page not found`
原因：Hermes 配置里 `base_url` 没走我们代理。修复：
```powershell
python fix_cli_proxy.py
# 或者在 GUI 里选目标服务商 → 勾选 Hermes → 勾代理 → 点🚀切换+启动
```

### Codex 报错 `Model provider \`codex\` not found`
原因：Codex 的 `model_provider="codex"` 是保留 ID，不能当自定义 provider。修复已经内置在切换逻辑：
- 走代理模式时自动写成 `model_provider = "openai"` + `base_url = http://127.0.0.1:3000/v1`
- 直连模式时根据服务商 `api_format` 自动选 `openai` / `anthropic` / `google`

### `python main.py` 启动后代理不起来
- 看日志框左下角状态 → 找 `[代理] listening on 127.0.0.1:3000`
- 或者 CLI：`python main.py serve --port 3000`

### 改默认端口
GUI：右侧 API 代理区 → 改端口 → 💾保存设置  
CLI：`python main.py serve --port 8787`

### PyInstaller / Nuitka 打包
```powershell
# PyInstaller：icon + data-file
pyinstaller --onefile --icon icon.ico --add-data "icon.ico;." --add-data "config.yaml;." main.py

# Nuitka：
python -m nuitka --standalone --onefile --enable-plugin=pyside6 --windows-icon-from-ico=icon.ico main.py
```
打包后 `config.yaml` / `icon.ico` 仍会在程序目录下正确读写。

---

## 🧰 CLI 命令速查

```
ccswith list                        # 列出所有服务商 / 当前选中
ccswith add <alias> <key> <host> <model> [--format {openai,anthropic,google}] [--display 显示名]
ccswith remove <alias>
ccswith switch <alias>              # 写入所有 CLI（自动备份 + 可选启动代理）
ccswith switch <alias> --proxy 3000 # 切换并启动代理 3000
ccswith backup                      # 立即快照
ccswith backups                     # 列出快照
ccswith rollback <backup>           # 回滚
ccswith import providers.json --merge
ccswith export providers.json
ccswith status                      # 打印当前 / 代理地址 / 虚拟模型
ccswith serve --host 127.0.0.1 --port 3000 --virtual-model virtual-model
ccswith hermes <alias> --proxy 3000
ccswith test --proxy 3000 --virtual-model virtual-model
ccswith -v <subcmd>                # 详细日志
```

---

## 🪵 日志系统

- 实时输出到界面右侧日志框
- 时间戳 + 颜色：`✅` 成功 / `❌` 失败 / `ℹ️` 提示 / `📊` 数据 / `📋` 信息
- 两个按钮：清空、复制（到剪贴板）
- 不写文件，仅界面 + 控制台

---

## 🎨 皮肤

界面顶栏 🎨 按钮可切 8 套皮肤 + 暗黑：
1. 皮肤1 粉灰 / 白灰
2. 皮肤2 蓝白 / 乳绿
3. 皮肤3 粉白 / 白粉
4. 皮肤4 白灰 / 白绿
5. 皮肤5 淡白 / 蓝灰白
6. 皮肤6 粉 / 乳粉
7. 皮肤7 乳白 / 乳绿
8. 暗黑

右键菜单 → 字体 / 字形 / 字号 / 颜色 → 💾保存到 config.yaml。

---

## 📄 许可

MIT License
