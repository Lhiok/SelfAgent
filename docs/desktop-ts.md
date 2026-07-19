# SelfAgent TypeScript 桌面

桌面入口为 **Electron + Python FastAPI**。唯一 UI 为 `src/web/static` SPA（浏览器与 Electron 共用）；`packages/desktop` 仅提供 Electron 主进程壳。

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
- `packages/desktop` — Electron 主进程（加载本地 FastAPI 提供的 web SPA）
- `src/web` — FastAPI + 唯一工作台 SPA

## 运行时流程

```
start.py → npm run start -w @selfagent/desktop
  → electron dist-electron/main.js
    → spawn python -m web
    → BrowserWindow.loadURL(http://127.0.0.1:8787/)
      → src/web/static/index.html
```
