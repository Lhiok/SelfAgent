# SelfAgent

面向各类项目的 Python 智能体核心：原生 tool calls、权限流水线、Plan Mode、MCP、Hooks、子 Agent 与可插拔 Skills。

本仓库按产品线分分支：

| 分支 | 用途 |
|------|------|
| `main` | 通用核心（当前） |
| `web` | 浏览器工作台 |
| `cli` | 命令行交互 |
| `app` | Electron + web SPA 桌面工作台 |

## 安装

```bash
cd SelfAgent
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,skills]"
copy config.example.yaml config.yaml   # 填入 ai.deepseek.api_key 等
```

可选 extra：`dev`（pytest）、`skills`（截屏 / Playwright）。一次装全：`pip install -e ".[all]"`。

**模块原理与架构（初学者）**：见 [docs/README.md](docs/README.md)。

## 快速使用

```python
import config as cfg
from agent import Agent
from skills import SkillRegistry

cfg.load_config("config.yaml")
agent = Agent(skills=SkillRegistry.from_config(), workdir=".")
print(agent.run("列出当前目录文件").answer)
```

连续对话：

```python
from session import Conversation
from agent import Agent

conv = Conversation(Agent(workdir="."))
print(conv.chat("先看项目结构").answer)
print(conv.chat("再读 README").answer)
```

示例脚本：`python examples/quickstart.py`、`python examples/chat_session.py`。

## 架构

```
Conversation / Agent
        │
   query_loop (agent/)
        │
 ┌──────┼──────────┐
 ▼      ▼          ▼
skills  MCP pool  run_subagent
        │
   permission + hooks
```

| 包 | 职责 |
|----|------|
| `agent/` | `Agent` 门面、`AgentEngine`、`query_loop`、工具编排 |
| `session/` | 连续对话、`RunControl`、doom-loop、细节输出 |
| `skills/` | 内置工具（文件、Git、Shell、语言工具链等） |
| `mcp/` | 外部 MCP 工具池（stdio） |
| `hooks/` | Hooks 事件与 command/http/prompt runners |
| `subagent/` | 子 Agent + sidechain JSONL |
| `permission/` | deny / ask / allow；Plan Mode 硬只读 |
| `plan/` / `memory/` / `workflow/` | 计划生命周期、记忆压缩、工作流 |
| `ai/` / `log/` / `feishu/` | 模型客户端、日志、飞书通知 |

配置入口：`config.yaml`（见 `config.example.yaml`）。Agent 相关项在 `react:` 节下（键名兼容，代码包为 `session` / `agent`）。

## 核心能力

- **Agent 循环**：原生 function/tool calls；`finish` 结束；`enter_plan_mode` / `submit_plan` 规划确认。
- **Plan Mode**：只读调研 → 提交计划 → 确认后由 workflow 执行；写入在规划期被硬拦截。
- **权限**：`deny` → `ask` → Hooks PreToolUse → plan/mode → `allow` / session grant → `default_effect`。
- **MCP**：`mcp.servers`（stdio）；工具名 `mcp__{server}__{tool}`。
- **Hooks**：`hooks:` 或 `.selfagent/hooks.yaml`；command **exit 2 = 拦截**。
- **子 Agent**：`run_subagent`；sidechain 于 `logs/sessions/{id}/subagents/`。

## Skills（摘要）

`local_file`、`search_code`、`shell_run`、`git_ops`、`ask_user`、`request_capability`、`nodejs` / `python` / `csharp` / `dotnet_build`、`web_fetch` / `http_request`、`diff_review`、`todo_tracker`、`screenshot` / `browser`、`feishu_notify`、`memory_ops`、`run_subagent`，以及循环控制 `finish` / `enter_plan_mode` / `submit_plan`。

## 扩展

- 新 AI：实现 `AIClient`，`register_provider(...)`
- 新 Skill：继承 `Skill`，`registry.register(...)`
- MCP：在 `mcp.servers` 增加 stdio 进程
- Hooks：在配置中注册事件与 runner

## 测试与回归

```bash
pytest
python -m regression
python -m regression --modules env,log,skills
python -m regression --live    # 需真实 API / 飞书
```
