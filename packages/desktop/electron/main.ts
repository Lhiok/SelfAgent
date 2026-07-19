/**
 * Electron 主进程：起 FastAPI 并打开工作台窗口（加载 web SPA）。
 */
import { app, BrowserWindow, shell } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../../..");

let apiProc: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;

function apiBase(): string {
  const port = process.env.SELFAGENT_PORT || "8787";
  const host = process.env.SELFAGENT_HOST || "127.0.0.1";
  return `http://${host}:${port}`;
}

function resolvePython(): string {
  if (process.env.SELFAGENT_PYTHON) return process.env.SELFAGENT_PYTHON;
  const venvPy = path.join(ROOT, ".venv", "Scripts", "python.exe");
  if (fs.existsSync(venvPy)) return venvPy;
  const venvUnix = path.join(ROOT, ".venv", "bin", "python");
  if (fs.existsSync(venvUnix)) return venvUnix;
  return process.platform === "win32" ? "python" : "python3";
}

async function waitHealth(base: string, timeoutMs = 45000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${base}/api/health`);
      if (r.ok) return true;
    } catch {
      /* retry */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

function startApi(): void {
  if (process.env.SELFAGENT_NO_SPAWN_API === "1") return;
  const py = resolvePython();
  const host = process.env.SELFAGENT_HOST || "127.0.0.1";
  const port = process.env.SELFAGENT_PORT || "8787";
  apiProc = spawn(
    py,
    ["-m", "web", "--host", host, "--port", port, "--no-open"],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        PYTHONPATH: [path.join(ROOT, "src"), process.env.PYTHONPATH || ""]
          .filter(Boolean)
          .join(path.delimiter),
      },
      stdio: "inherit",
      windowsHide: true,
    },
  );
  apiProc.on("exit", (code) => {
    console.log(`[selfagent] API exited code=${code}`);
    apiProc = null;
  });
}

function stopApi(): void {
  if (apiProc && !apiProc.killed) {
    apiProc.kill();
    apiProc = null;
  }
}

async function createWindow(): Promise<void> {
  const base = apiBase();
  startApi();
  const ok = await waitHealth(base);
  if (!ok) {
    console.error(`[selfagent] API not ready at ${base}`);
  }

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 560,
    title: "SelfAgent",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  await mainWindow.loadURL(`${base}/`);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  void createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
});

app.on("window-all-closed", () => {
  stopApi();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  stopApi();
});
