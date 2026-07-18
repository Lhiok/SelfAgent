"""命令行入口：python -m regression"""

from __future__ import annotations

import argparse
import json
import sys

import config as cfg
from regression.runner import run_regression


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SelfAgent 模块回归")
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认自动查找 config.yaml）",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="包含需要真实密钥的在线用例（DeepSeek / 飞书）",
    )
    parser.add_argument(
        "--modules",
        default="",
        help="仅跑指定模块，逗号分隔，如 env,log,skills,react,ai,feishu",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出报告",
    )
    args = parser.parse_args(argv)

    if args.config:
        cfg.load_config(args.config)
    else:
        cfg.load_config()

    modules = [m.strip() for m in args.modules.split(",") if m.strip()] or None
    # --live 显式开启；未传则走配置 regression.include_live
    include_live = True if args.live else None
    report = run_regression(include_live=include_live, modules=modules)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.summary())

    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
