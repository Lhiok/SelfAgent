# AI：模型客户端

`src/ai/` 抽象大模型调用，使 `query_loop` 不绑死某一厂商。当前内置 DeepSeek（OpenAI 兼容 chat + tools）。

## 核心类型（`base.py`）

| 类型 | 含义 |
|------|------|
| `AIClient` | 抽象：`chat` / 可选 `chat_stream` / `ask` |
| `AIMessage` | `role` + `content`，以及 tool 相关字段（`tool_calls`、`tool_call_id`、`name`） |
| `ToolCall` | 模型发起的一次函数调用 |
| `ChatOptions` | model / temperature / **tools** / tool_choice 等 |
| `AIResponse` | 文本 + `tool_calls` + usage |
| `StreamEvent` | 流式增量 |

## 工厂

```python
from ai import create_ai_client

client = create_ai_client("deepseek")  # 读 ai.deepseek.*
print(client.ask("用一句话介绍你自己"))
```

`factory.py` 的 `register_provider("name", Cls)` 可挂自研客户端。

## 和 query_loop

每一步大致：

```text
messages + tools schema
    → AIClient.chat(..., ChatOptions(tools=..., tool_choice="auto"))
    → AIResponse.tool_calls / content
```

有 tools 时优先走非流式或内部降级策略（视提供商实现）；纯文本收尾也可流式展示（产品线 UI）。

## 测试不耗额度

`tests/scripted_ai.py` 的 `ScriptedAI` 按预设 `tool_calls` 返回，是阅读循环的最佳搭档：

```python
from scripted_ai import ScriptedAI, finish, resp, tc

ai = ScriptedAI([
    resp(tc("local_file", {"action": "list", "path": "."})),
    resp(finish("列完了")),
])
```

## 配置要点

`ai.deepseek`：`api_key`、`base_url`、`model`、`timeout`、`retries`、`stream`。  
密钥只放本地 `config.yaml`（已 gitignore），不要提交仓库。

相关：[agent.md](agent.md)、[architecture.md](architecture.md)。
