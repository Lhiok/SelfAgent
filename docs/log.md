# Log：日志与调试通道

`src/log/` 提供分级日志、多 sink（控制台 / 文件 / 服务器），以及可选的 **debug 会话文件**（按分类过滤，便于排查 api / skills / hooks）。

## 怎么用

```python
from log import get_logger

log = get_logger("my.module")
log.notice("启动")
log.warning("磁盘将满")
log.critical("不可用")
```

级别（由低到高）：`verbose` → `debug` → `notice` → `warning` → `critical`。  
配置：`config.yaml` → `log.level`、`log.modes`（`console` / `file` / `server`）。

## 文件与运行隔离

启用 `file` mode 时，每次进程运行写入独立文件（`logs/runs/...`），避免多进程互相覆盖。  
`log.file.path` 主要用于确定目录。

## Debug 通道（进阶）

| 模块 | 作用 |
|------|------|
| `debug.py` / `debug_filter.py` | 会话向 debug 文件写分类日志；`--debug=api,skills` 或 `!otel` 过滤 |
| `buffer.py` | 缓冲 |
| `errors.py` / `sinks.py` | 错误捕获与 sink 初始化 |

环境变量示例（与配置等价思路）：

- `SELFAGENT_DEBUG=1`
- `SELFAGENT_DEBUG_FILTER=api,skills,!feishu`

Agent / skills / permission 在关键路径会 `get_logger(...)`，打开 detail 或 debug 后更容易看清「模型要了什么工具、权限为何拒绝」。

## 和架构的关系

Log **不参与** 决策，但是读懂运行原理的眼睛。排查顺序建议：

1. `react.detail: summary|full`  
2. 应用日志 `notice/warning`  
3. debug filter 针对 `skills` / `hooks`  

测试：`tests/test_log_debug.py`。

相关：[architecture.md](architecture.md)、[agent.md](agent.md)。
