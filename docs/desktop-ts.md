# SelfAgent TypeScript 桌面

Qt（PySide6）控件层已移除。桌面入口为 **Electron + Python FastAPI**，UI 默认加载 `src/web/static` SPA；另提供 React 工作台源码于 `packages/desktop/src`（`npm run dev -w @selfagent/desktop` 可单独开发）。

## 参考（仅架构学习，未拷贝源码）

- `C:\Users\Lhiok\Desktop\Github\JackProAi-claudecode3.1` — 启动器 / env / 本地部署体验
- `C:\Users\Lhiok\Desktop\Github\claude-code-rev` — QueryEngine 式事件消费、工具流式展示思路

## 启动

```bat
start.bat
```

或：

```powershell
.\start.ps1
# 仅浏览器
.\start.ps1 --browser
```

依赖：

- Python：`pip install -e ".[ui]"`
- Node 20+：仓库根目录 `npm install`

## 包结构

- `packages/client` — 类型化 REST/SSE 客户端
- `packages/desktop` — Electron 主进程 + React UI 源码
- `src/web` — FastAPI + 现网 SPA
