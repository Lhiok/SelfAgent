# SelfAgent（app 分支）

本分支提供 **Windows 独立窗口应用**（PySide6），区别于浏览器 `web` 与终端 `cli`。通用核心与 `main` 对齐。

## 启动

```bash
pip install -e ".[app]"
start.bat
# 或: python start.py
# 或: python -m app
# 或: selfagent-app
```

原生 PySide6 工作台，功能对齐 `web` 浏览器端（不内嵌网页）：

- 多工作区 / 多会话侧栏（收藏、归档、折叠、重命名）
- Agent / Plan、细节级别、流式步骤与工具摘要
- 待确认计划、文件 diff 检视、打开本地文件
- 多题 `ask_user`、todo 进度条
- 会话落盘：`logs/workspaces/`（与 web 共用格式，可互通）

可选打包：

```bash
pip install pyinstaller
pyinstaller -F -w -n SelfAgent src/app/__main__.py
```

---

## 快速使用

```python
import config as cfg
from log import get_logger
from feishu import FeishuBot
from ai import create_ai_client
from react import ReActAgent
from skills import SkillRegistry

cfg.load_config("config.yaml")
log = get_logger()

# 日志
log.notice("启动")
log.warning("磁盘将满")
log.critical("数据库不可用")

# 飞书
FeishuBot().send_text("任务完成")

# AI（读取 ai.deepseek.*）
client = create_ai_client("deepseek")
print(client.ask("用一句话介绍你自己"))

# ReAct + Skill（受 permission 角色限权）
agent = ReActAgent(
    skills=SkillRegistry.from_config(),
    role="readonly",
    workdir=".",  # 可选：文件/搜索/命令/Git 的工作根目录
)
result = agent.run("查看当前目录有哪些文件")
print(result.answer)
# 打开细节后可看每轮 Thought/Action/Observation：
# print(result.detail_text) 或 result.format_detail("full")
```

运行示例：

```bash
python examples/quickstart.py
python examples/chat_session.py --workdir D:\my-project
```

## 工作目录

Agent 可设定工作目录，并同步到带 `root` 的 Skill（如 `local_file` / `search_code` / `shell_run` / `git_ops` / `dotnet_build` / `nodejs` / `python` / `csharp` / `diff_review` / `todo_tracker` / `screenshot` / `browser`）：

```python
agent = ReActAgent(workdir="/path/to/project")
agent.set_workdir("./other")       # 运行时切换
conv.set_workdir("./other")        # Conversation
```

可选配置 `react.workdir`；`chat_session` 支持 `--workdir` / `-C` 与命令 `/workdir <路径>`。

## 运行日志隔离

启用 `log.modes` 含 `file` 时，每次进程运行写入独立文件（`logs/runs/时间_pid.log`），互不混写。`log.file.path`（如 `logs/app.log`）仅用于确定目录。可用 `get_run_log_path()` 查看本次路径。

## 过程细节开关

默认只输出最终答案（盲盒）。在 `config.yaml` → `react` 打开过程细节：

| `detail` | 效果 |
|----------|------|
| `off` | 仅 Final Answer（默认） |
| `summary` | 每轮 Thought + Action（含简短 Input） |
| `full` | 另含完整 Action Input / Observation |

```yaml
react:
  detail: summary          # off | summary | full
  stream_detail: true      # 每轮执行中实时打印
  detail_max_chars: 2000   # 单段截断，防刷屏
```

也可代码或会话内切换：

```python
agent = ReActAgent(detail="full")          # 构造时
agent.set_detail("summary")                # 运行时
conv.set_detail("full")                    # Conversation
# chat_session 示例支持命令: /detail off|summary|full
```

`stream_detail: true` 时边跑边打印；为 `false` 时可在结束后读 `result.detail_text`。

## 目录结构

```
src/
  config.py          # YAML 配置加载
  env/               # 环境层
  log/               # 日志层
  feishu/            # 飞书层
  ai/                # AI 层（deepseek）
  react/             # ReAct 框架层
  skills/            # Skill 能力层（local_file）
  permission/        # 权限控制层（ReAct → Skill 限权）
  regression/        # 回归层（模块功能验证）
```

## 权限层

通过 `config.yaml` → `permission` 配置角色，限制 ReAct 可调用的 Skill 与 action：

| 角色示例 | 能力 |
|----------|------|
| `readonly` | `local_file` 仅 `list` / `read` |
| `editor` | `local_file` 全部操作 |
| `admin` | 全部 Skill / action |

```python
from permission import PermissionGuard
from react import ReActAgent

# 使用配置中的角色
agent = ReActAgent(role="readonly")

# 或代码注入守卫
guard = PermissionGuard.from_config(role="editor")
agent = ReActAgent(permission=guard)
```

未配置 `permission` 节时默认不限权。

## 连续对话与历史恢复

用 `Conversation` 保持多轮上下文；默认自动保存到 `logs/sessions/`。

```python
from react import Conversation, ReActAgent

conv = Conversation(ReActAgent())
print(conv.chat("先查看项目结构").answer)
print(conv.chat("根据刚才结果，再读 README").answer)  # 带历史
conv.confirm_plan()  # 若上一轮产出了计划

# 恢复
path = Conversation.resolve_session_path("latest")
conv2 = Conversation.load(path, agent=ReActAgent())
# 或: conv.resume(path)
Conversation.list_sessions()  # 列出历史
```

交互示例：

```bash
python examples/chat_session.py
python examples/chat_session.py --list-sessions
python examples/chat_session.py --resume latest
python examples/chat_session.py --resume 2723e299
```

会话内命令：`/sessions`、`/load <路径|id|latest>`、`/save`。

### 进度事件 / 取消 / 压缩

- `ReActAgent(on_progress=...)` 会收到 `status` / `step` / `assistant_delta` / `skill` / `cancelled` 等事件，供 CLI/Web 实时展示。
- `Conversation.cancel()` 请求结束当前 turn；`enqueue(text)` 在步间注入中途补充。
- 历史超过 `react.conversation.compact_after_messages` 时自动摘要压缩；也可 `conv.compact_now()`。
- 重复同一工具调用或连续失败由 `react.doom_loop` 干预/停止。
- 工具 Observation 经 `SkillBridge` 统一截断（`skills.output_max_chars`）；`shell_run` 支持协作取消。

## Plan Mode

先规划、后执行。Plan Mode 下默认可只读探测（`list`/`read`），写入类操作会被拦截，只能写入计划步骤。

```python
from react import AgentMode, ReActAgent

# 方式 1：构造时指定
planner = ReActAgent(mode=AgentMode.PLAN)
plan_result = planner.plan("给项目加一个 README 小节")
print(plan_result.plan.format_text())

# 方式 2：配置 react.mode: plan 后直接 run
# 批准后执行计划
agent = ReActAgent(mode=AgentMode.AGENT)
exec_result = agent.execute_plan(plan_result.plan)
print(exec_result.answer)
```

计划步骤格式：

```text
Plan:
1. skill=local_file | input={"action":"read","path":"a.py"} | why=了解现状
2. skill=local_file | input={"action":"patch",...} | why=修改
Final Answer: 计划摘要
```

## Skill：local_file

| action | 说明 |
|--------|------|
| `list` | 列出目录 |
| `read` | 读取文件 |
| `write` | 写入/覆盖文件 |
| `patch` | 将唯一匹配的 `old_text` 替换为 `new_text` |
| `move` | 移动/重命名：`path` 源路径，`dest` 目标路径；可选 `overwrite` |

默认根目录与是否允许写入见 `config.yaml` → `skills.local_file`。

## Skill：ask_user

Plan Mode 落地前向用户确认方案细节。支持序号选择、多选、自定义输入；可注入 `ask_handler`（测试/GUI/飞书）。

```python
from skills import AskUserSkill

skill = AskUserSkill()  # 默认命令行交互
# 或: AskUserSkill(ask_handler=lambda q, opts, meta: "1")
result = skill.run(
    question="日志落盘选哪种？",
    options=["仅控制台", "控制台+文件", "控制台+文件+上报"],
    default="2",
)
print(result.output)  # JSON：selected / indexes / raw
```

Plan Mode 下默认允许调用（`react.plan.allow_skills`），写入类操作仍被拦截。

## Skill：request_capability

当 Agent 认定现有 Skill 无法满足需求时，向用户提交能力需求（**不会立即实现**）：在本地目录写入 Markdown 需求文档，并可选飞书通知。提交后 Agent 应继续用现有 Skill 推进任务。

配置见 `config.yaml` → `skills.request_capability`（`output_dir` / `notify_feishu`）。

```json
{
  "title": "需要浏览器自动化",
  "need": "打开网页并截图",
  "why": "现有 shell_run / local_file 无法操控浏览器",
  "context": "任务：验证登录页",
  "workaround": "先输出手工步骤文档",
  "priority": "high",
  "notify": true
}
```

Plan Mode 下默认允许调用（`react.plan.allow_skills`）。

## Skill：search_code

项目内关键词/正则搜索，适合定位实现。

```json
{"action":"search","query":"ReActAgent","extensions":["py"],"max_results":20}
```

## Skill：shell_run

受控执行白名单命令（默认禁止管道/重定向/`&&` 等）。Plan Mode 下默认不执行，仅可写入计划后由 agent 执行。

```json
{"action":"run","command":"pytest -q","cwd":"."}
```

## Skill：feishu_notify

推送文本到飞书机器人（需配置 Webhook）。

```json
{"action":"text","text":"计划已生成，请确认"}
{"action":"markdown","title":"任务完成","content":"**结果**：通过"}
```

## Skill：git_ops

Git 仓库操作。默认只读；`allow_write: true` 后才可 `add`/`commit`。不提供 push/pull/force。

| action | 说明 |
|--------|------|
| `status` / `diff` / `log` / `branch` / `show` | 只读 |
| `add` / `commit` | 写入（需配置 + 权限） |

```json
{"action":"status"}
{"action":"diff","staged":false}
{"action":"log","max_count":10}
{"action":"add","paths":["src/foo.py"]}
{"action":"commit","message":"fix: handle empty config"}
```

## Skill：dotnet_build

在工作目录执行 `dotnet restore/build/test`（需本机 .NET SDK）。

```json
{"action":"build","project":"MyApp.sln","configuration":"Debug"}
{"action":"test","cwd":"src/MyApp.Tests"}
```

## Skill：nodejs / python / csharp

三种语言的专用工具链（比通用 `shell_run` 更安全、语义更清晰）。

| Skill | 常用 action |
|--------|-------------|
| `nodejs` | `install` / `test` / `build` / `script` / `run` / `version` |
| `python` | `run` / `test` / `install` / `module` / `compile` / `version` |
| `csharp` | `restore` / `build` / `test` / `run` / `format` / `version` |

```json
{"action":"test","cwd":"."}
{"action":"run","file":"scripts/hello.py"}
{"action":"script","script":"lint","package_manager":"npm"}
{"action":"build","project":"App.sln","configuration":"Release"}
```

`csharp` 覆盖 run/format；轻量编译仍可用 `dotnet_build`。

## Skill：web_fetch / http_request

抓取公开网页正文，或发送通用 HTTP 请求。默认禁止内网（防 SSRF）；`allow_private: true` 可放开。

```json
{"action":"fetch","url":"https://example.com/docs"}
{"action":"get","url":"https://httpbin.org/get"}
{"action":"post","url":"https://httpbin.org/post","json_body":{"ok":true}}
```

## Skill：diff_review

对 git diff / 补丁文本做静态复盘（文件统计 + 风险关键词）。

```json
{"action":"review","source":"git","staged":false}
{"action":"review","source":"text","diff":"diff --git a/x b/x\n+password = secret"}
```

## Skill：todo_tracker

跨步骤任务清单，落盘 `.selfagent/todos.json`。规范生命周期：`add` → `start`（进行中）→ 真正干活 → `complete`。未 `start` 不能直接 `complete`（除非 `force:true`）。Web 工作台会在对话下方展示进度条。

```json
{"action":"add","items":["修编译错误","补单测"]}
{"action":"start","id":"abc123","note":"开始修编译"}
{"action":"complete","id":"abc123"}
{"action":"list"}
```

## Skill：screenshot

截屏保存到工作目录，或读取已有图片尺寸。优先 `mss`+`Pillow`；Windows 可走 PowerShell 兜底。

```json
{"action":"capture","path":"logs/screenshots/ui.png"}
{"action":"read","path":"logs/screenshots/ui.png"}
```

## Skill：browser

Playwright 无头浏览器（可选依赖：`pip install playwright && playwright install chromium`）。

```json
{"action":"goto","url":"https://example.com"}
{"action":"content"}
{"action":"screenshot","path":"logs/browser/page.png"}
```

## 扩展

- **新 AI 提供商**：实现 `AIClient`，再 `register_provider("name", Cls)`
- **新 Skill**：继承 `Skill`，`registry.register(YourSkill())`
- **新日志模式**：在 `Logger` 中增加 mode 分支，或组合现有 modes

## 回归层

默认离线验证 env / log / skills / react / ai(工厂) / feishu(缺配置行为)：

```bash
python -m regression
# 或
selfagent-regression
# 或
python examples/run_regression.py
```

仅测部分模块：

```bash
python -m regression --modules env,log,skills
```

包含 DeepSeek / 飞书真实连通性（需已配置密钥）：

```bash
python -m regression --live
```

JSON 报告：

```bash
python -m regression --json
```

代码调用：

```python
from regression import run_regression

report = run_regression(include_live=False)
print(report.summary())
assert report.ok
```

## 测试

```bash
pytest
```
