"""启动本地对话工作台：python -m web / selfagent-ui"""

from __future__ import annotations

import argparse
import logging
import re
import webbrowser


class _QuietSessionGetFilter(logging.Filter):
    """过滤会话详情的成功轮询访问日志，避免刷屏。"""

    _pat = re.compile(r'"GET /api/sessions/[^"\s]+ HTTP/[^"]+" 200\b')

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        return self._pat.search(msg) is None


def main() -> None:
    parser = argparse.ArgumentParser(description="SelfAgent 本地对话工作台")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8787, help="端口")
    parser.add_argument(
        "--config",
        default=None,
        help="config.yaml 路径（默认自动查找）",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="不自动打开浏览器",
    )
    args = parser.parse_args()

    import config as cfg

    if args.config:
        cfg.load_config(args.config)
    else:
        cfg.load_config()

    from log import reset_logger

    reset_logger()

    try:
        from hooks import HookRegistry, set_hook_registry

        set_hook_registry(HookRegistry.from_config())
    except Exception:  # noqa: BLE001
        pass

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "缺少 UI 依赖，请先安装: pip install -e \".[ui]\""
        ) from exc

    url = f"http://{args.host}:{args.port}/"
    print(f"SelfAgent Workbench → {url}")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    logging.getLogger("uvicorn.access").addFilter(_QuietSessionGetFilter())

    uvicorn.run(
        "web.app:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
