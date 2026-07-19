"""一键启动：优先 Electron 桌面；失败则回退浏览器打开 web 工作台。

也可：selfagent-app / python -m web
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _run_electron() -> int:
    npm = shutil.which("npm")
    if not npm:
        return 1
    desktop = _ROOT / "packages" / "desktop"
    if not (desktop / "node_modules").is_dir() and not (_ROOT / "node_modules").is_dir():
        print("正在安装 Node 依赖（首次较慢）…")
        r = subprocess.run([npm, "install"], cwd=str(_ROOT))
        if r.returncode != 0:
            return r.returncode
    env = os.environ.copy()
    env.setdefault("SELFAGENT_HOST", "127.0.0.1")
    env.setdefault("SELFAGENT_PORT", "8787")
    # Electron 内会再 spawn API；此处不重复起服务
    return subprocess.call(
        [npm, "run", "start", "-w", "@selfagent/desktop"],
        cwd=str(_ROOT),
        env=env,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="SelfAgent 桌面入口")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="仅启动 web API 并用系统浏览器打开（不启动 Electron）",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-open", action="store_true")
    args, rest = parser.parse_known_args()

    if args.browser or os.environ.get("SELFAGENT_BROWSER") == "1":
        from web.__main__ import main as web_main

        sys.argv = [
            "web",
            "--host",
            args.host,
            "--port",
            str(args.port),
            *(["--no-open"] if args.no_open else []),
            *rest,
        ]
        web_main()
        return

    code = _run_electron()
    if code == 0:
        return
    print("Electron 启动失败，回退到浏览器模式…", file=sys.stderr)
    from web.__main__ import main as web_main

    sys.argv = [
        "web",
        "--host",
        args.host,
        "--port",
        str(args.port),
        *(["--no-open"] if args.no_open else []),
    ]
    web_main()


if __name__ == "__main__":
    main()
