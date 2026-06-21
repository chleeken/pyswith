# SkillOpt GUI - 图形化技能优化工具

基于 Microsoft [SkillOpt](https://github.com/microsoft/SkillOpt) 项目开发的图形化界面程序。

SkillOpt 是一种**文本空间优化器**，通过轨迹驱动的编辑、验证门控更新和可部署的 `best_skill.md` 工件，为冻结的 LLM 代理训练可重用的自然语言技能。

## 功能特点

- **完整的图形界面**：基于 Tkinter 构建，支持所有配置参数的图形化管理
- **多后端支持**：OpenAI / Azure OpenAI / Claude / Qwen / MiniMax
- **训练管道**：6 阶段训练循环（Rollout → Reflect → Aggregate → Select → Update → Evaluate）
- **验证门**：基于验证集的接受/拒绝决策
- **配置管理**：YAML 配置文件的加载和保存
- **实时日志**：带时间戳和彩色标签的日志显示
- **技能编辑器**：内置 Markdown 文档编辑器
- **皮肤切换**：8 套界面皮肤
- **多语言**：中文/English 切换

## 快速开始

### 安装依赖

```bash
pip install pyyaml openai
```

### 启动程序

```bash
python launch_skillopt.py
```

或直接：

```bash
python skillopt_app.py
```

### 使用流程

1. **配置环境**：在「环境」标签页选择 benchmark 和数据路径
2. **配置模型**：在「模型」标签页设置后端和 API Key
3. **配置训练**：在「训练」和「优化器」标签页调整参数
4. **编辑技能**：在中央面板编写或加载初始技能文档
5. **开始训练**：点击「开始训练」按钮
6. **评估**：训练完成后点击「运行评估」

## 配置文件

配置文件保存在 `config/skillopt_config.yaml`，支持所有 SkillOpt 训练参数。

## 环境支持

支持以下 benchmark 环境：
- SearchQA, ALFWorld, LiveMathematicianBench
- SpreadsheetBench, BabyVision, DocVQA
- MathVerse, OfficeQA, SealQA, MMRB, SWE-Bench

## 项目结构

```
├── skillopt_app.py          # 主程序（GUI + 核心逻辑）
├── launch_skillopt.py       # 启动脚本
├── config/
│   ├── skillopt_config.yaml # 默认配置
│   └── searchqa_initial.md  # SearchQA 初始技能
└── outputs/                 # 训练输出
```

## 更多信息

- [SkillOpt 项目主页](https://github.com/microsoft/SkillOpt)
- [论文](https://arxiv.org/abs/2605.23904)
- [项目文档](https://microsoft.github.io/SkillOpt/)
