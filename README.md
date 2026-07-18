# SelfAgent

SelfAgent：面向各类项目的 Python 智能体基础框架，按层解耦：

| 层级 | 能力 |
|------|------|
| 环境层 | 按「配置 / 用户环境变量 / 系统环境变量 / 进程环境」查找（可选回退） |
| 日志层 | 提醒 / 警告 / 严重；控制台、本地文件、服务器上报 |
| 飞书层 | 自定义机器人 Webhook 推送（文本 / Markdown 卡片 / 富文本） |
| AI 层 | 统一客户端接口，按配置选择模型；当前实现 DeepSeek |
| ReAct 层 | Thought → Action → Observation；Plan Mode；连续对话 |
| Skill 层 | 可插拔工具；本地文件、搜索、命令、飞书、Git、用户确认、能力需求提交等 |
| 权限层 | 按角色限制 ReAct 可调用的 Skill / action |
| 回归层 | 一键验证各模块功能（离线默认，可选在线） |

## 安装

```bash
cd SelfAgent
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy config.example.yaml config.yaml
```

在对应模块节中配置（比集中写在 `env` 更易维护）：

```yaml
ai:
  deepseek:
    api_key: "sk-xxx"
    base_url: "https://api.deepseek.com"
    model: "deepseek-chat"

feishu:
  webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
```

未填写时，仍可回退到同名系统/用户环境变量（如 `DEEPSEEK_API_KEY`）。

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
agent = ReActAgent(skills=SkillRegistry.from_config(), role="readonly")
result = agent.run("查看当前目录有哪些文件")
print(result.answer)
# 打开细节后可看每轮 Thought/Action/Observation：
# print(result.detail_text) 或 result.format_detail("full")
```

运行示例：

```bash
python examples/quickstart.py
```

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

## 连续对话

用 `Conversation` 保持多轮上下文，适合把一个任务拆成多轮推进：

```python
from react import Conversation, ReActAgent

conv = Conversation(ReActAgent())
print(conv.chat("先查看项目结构").answer)
print(conv.chat("根据刚才结果，再读 README").answer)  # 带历史
conv.confirm_plan()  # 若上一轮产出了计划
conv.reset()
# conv.save() / Conversation.load(path)
```

交互示例：

```bash
python examples/chat_session.py
```

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
