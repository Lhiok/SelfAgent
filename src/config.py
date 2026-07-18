"""全局配置加载。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_config: dict[str, Any] = {}
_config_path: Path | None = None
_loaded: bool = False


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载 YAML 配置。未指定路径时按常见位置查找。"""
    global _config, _config_path, _loaded

    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        cwd = Path.cwd()
        candidates.extend(
            [
                cwd / "config.yaml",
                cwd / "config.yml",
                Path(__file__).resolve().parents[1] / "config.yaml",
                Path(__file__).resolve().parents[1] / "config.yml",
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError(f"配置文件格式错误，需为对象: {candidate}")
            _config = data
            _config_path = candidate
            _loaded = True
            return _config

    _config = {}
    _config_path = None
    _loaded = True
    return _config


def get_config() -> dict[str, Any]:
    """返回已加载配置；若尚未加载则尝试自动加载。"""
    if not _loaded:
        load_config()
    return _config


def get_section(name: str, default: Any = None) -> Any:
    return get_config().get(name, default if default is not None else {})


def set_config(data: dict[str, Any]) -> None:
    """测试或运行时注入配置。"""
    global _config, _config_path, _loaded
    _config = dict(data)
    _config_path = None
    _loaded = True


def config_path() -> Path | None:
    return _config_path
