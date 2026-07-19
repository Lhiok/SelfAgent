# SelfAgent

Python 智能体核心 + 本地工作台。模型通过原生 tool calls 调用内置 Skill / MCP 工具，配合权限、Hooks、Plan Mode 与子 Agent 完成任务。

唯一 UI 为 `src/web/static` SPA（浏览器与 Electron 共用）。桌面细节见 [docs/desktop-ts.md](docs/desktop-ts.md)。

## 快速开始

```bash
# 依赖
pip install -e ".[ui]"
npm install                     # 桌面壳需要 Node 20+
cp config.example.yaml config.yaml   # 填入 ai.deepseek.api_key 等

# 启动工作台
start.bat                       # 或: python start.py / selfagent-app
python start.py --browser       # 仅浏览器（不启 Electron）
```

浏览器默认打开 `http://127.0.0.1:8787/`。Electron 会自动拉起同一 FastAPI 服务并加载该 SPA。

CLI 示例：

```bash
python examples/quickstart.py
python examples/chat_session.py --workdir .
python examples/chat_session.py --resume latest
```

库用法：

```python
import config as cfg
from agent import Agent
from skills import SkillRegistry

cfg.load_config("config.yaml")
agent = Agent(skills=SkillRegistry.from_config(), workdir=".")
print(agent.run("列出当前目录文件").answer)
```

## 架构一览

```
Electron 壳 ──► FastAPI (src/web) ──► web/static SPA
                      │
                      ▼
              Conversation / Agent
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
    query_loop    permission     hooks
         │
    ┌────┴────┬──────────┐
    ▼         ▼          ▼
  skills    MCP pool   run_subagent
```

| 目录 | 职责 |
|------|------|
| `src/agent/` | `Agent` 门面、`AgentEngine`、`query_loop`、工具编排 |
| `src/session/` | 连续对话、`RunControl`、doom-loop、细节输出 |
| `src/skills/` | 内置工具（文件、Git、Shell、语言工具链等） |
| `src/mcp/` | 外部 MCP 工具池（stdio） |
| `src/hooks/` | 完整 Hooks 事件与 command/http/prompt runners |
| `src/subagent/` | 子 Agent + sidechain JSONL |
| `src/permission/` | deny / ask / allow 流水线；Plan Mode 硬只读 |
| `src/plan/`、`src/memory/`、`src/workflow/` | 计划生命周期、记忆压缩、工作流 |
| `src/web/` | FastAPI + 唯一 SPA |
| `packages/desktop/` | Electron 主进程壳 |
| `packages/client/` | 类型化 REST/SSE 客户端 |

配置入口：`config.yaml`（参考 `config.example.yaml`）。Agent 相关项仍在 `react:` 节下。

## 核心能力

### Agent 循环

原生 function/tool calls。任务结束须调用 `finish`；复杂改动可先 `enter_plan_mode`，再 `submit_plan` 交用户确认。同一步内只读工具可并行（`react.max_tool_concurrency`）。

过程细节：`react.detail` = `off` | `summary` | `full`。也可用 `agent.set_detail(...)` / 会话命令 `/detail`。

### 连续对话

`Conversation` 保持多轮上下文，默认落盘 `logs/sessions/`。支持取消、中途插话、历史压缩、会话恢复。

```python
from session import Conversation
from agent import Agent

conv = Conversation(Agent(workdir="."))
print(conv.chat("先看项目结构").answer)
print(conv.chat("再读 README").answer)
```

### Plan Mode

只读调研 → 提交计划 → 用户确认后执行。写入类操作在 Plan Mode 下被硬拦截，只能进入计划步骤。

### 权限

`permission.rules`：`deny` → `ask` → Hooks PreToolUse → Plan/mode → `allow` / session grant → `default_effect`。

规则形如 `local_file(write)`、`mcp__*`、`run_subagent`。Web/CLI 命中 `ask` 时可交互授权，并用 `grant_session` 本轮放行。

### MCP

在 `config.yaml` 配置 stdio 服务器后，工具进入 loop，命名为 `mcp__{server}__{tool}`。

```yaml
mcp:
  servers:
    echo:
      transport: stdio
      command: python
      args: ["-m", "tests.fixtures.mcp_echo_server"]
```

- `GET /api/mcp` — 列表  
- `POST /api/mcp/reload` — 重连并刷新工具  

### 子 Agent

内置工具 `run_subagent`（`prompt` / `description` / `max_steps` / 可选 `agent_id` 续跑）。

Sidechain：`logs/sessions/{session_id}/subagents/agent-{id}.jsonl` + `.meta.json`。进度事件：`subagent_start` / `subagent_progress` / `subagent_stop`。

### Hooks

配置 `hooks:` 或 `.selfagent/hooks.yaml`。Runner：`command`（**exit 2 = 拦截**）、`http`、`prompt`（stdout 注入上下文）。

已接线事件包括：`PreToolUse`、`PostToolUse` / `PostToolUseFailure`、`UserPromptSubmit`、`Stop` / `StopFailure`、`SubagentStart` / `SubagentStop`、`PreCompact` / `PostCompact`、`PermissionRequest` 等（类型表齐全；无触发点的事件可注册为 no-op）。

```yaml
hooks:
  PreToolUse:
    - matcher: local_file
      runner: command
      command: 'python path/to/guard.py'   # exit 2 则拒绝工具
```

## 内置 Skills（摘要）

| Skill | 用途 |
|-------|------|
| `local_file` | list / read / write / patch / move |
| `search_code` | 项目内搜索 |
| `shell_run` | 白名单命令 |
| `git_ops` | 状态/diff/log；可选 add/commit |
| `ask_user` | 向用户提问（可暂存批量确认） |
| `request_capability` | 落盘能力需求文档 |
| `nodejs` / `python` / `csharp` / `dotnet_build` | 语言工具链 |
| `web_fetch` / `http_request` | 抓取与 HTTP（默认禁内网） |
| `diff_review` | diff 静态复盘 |
| `todo_tracker` | 步骤清单（`.selfagent/todos.json`） |
| `screenshot` / `browser` | 截屏 / Playwright（可选依赖） |
| `feishu_notify` | 飞书机器人 |
| `memory_ops` | 文件型记忆 |
| `run_subagent` | 子 Agent（见上） |
| `finish` / `enter_plan_mode` / `submit_plan` | 循环控制 |

工作目录 `Agent(workdir=...)` 会同步到带 `root` 的 Skill。可选依赖：`pip install -e ".[skills]"`（截屏、浏览器等）。

## 扩展

- **AI 提供商**：实现 `AIClient`，`register_provider("name", Cls)`
- **Skill**：继承 `Skill`，`registry.register(...)`
- **MCP**：在 `mcp.servers` 增加 stdio 进程即可，无需改核心代码
- **Hooks**：在配置中注册事件与 runner

## 测试与回归

```bash
pytest
python -m regression              # 或 selfagent-regression
python -m regression --modules env,log,skills
python -m regression --live       # 需真实 API / 飞书配置
python -m regression --json
```

## 许可与状态

个人/项目用智能体框架，版本见 `pyproject.toml`。插件市场、子 Agent worktree/远程 swarm、完整 MCP OAuth 生态不在当前范围。
