"""截屏或读取本地图片元数据。"""

from __future__ import annotations

import base64
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.screenshot")


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return int(w), int(h)


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i < len(data) - 8:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in {0xC0, 0xC1, 0xC2}:
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return int(w), int(h)
        if marker == 0xD9:
            break
        if marker == 0x00 or marker == 0x01 or 0xD0 <= marker <= 0xD9:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + length
    return None


class ScreenshotSkill(Skill):
    name = "screenshot"
    description = (
        "截取屏幕并保存到工作目录，或读取已有图片的尺寸/预览信息。"
        "capture 优先使用可选依赖 mss/Pillow；未安装时返回安装提示。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["capture", "read"],
                "default": "capture",
            },
            "path": {
                "type": "string",
                "description": "read 时的图片路径；capture 时可指定保存相对路径",
            },
            "monitor": {
                "type": "integer",
                "default": 1,
                "description": "mss 显示器编号，1=主屏",
            },
            "include_base64": {
                "type": "boolean",
                "default": False,
                "description": "是否在 data 中附带小图 base64（限小文件）",
            },
            "max_base64_bytes": {"type": "integer", "default": 200000},
        },
        "required": [],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        output_dir: str = "logs/screenshots",
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.output_dir = output_dir
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="screenshot 已在配置中禁用")
        action = str(kwargs.get("action") or "capture").strip().lower()
        if action == "capture":
            return self._capture(kwargs)
        if action == "read":
            return self._read(kwargs)
        return SkillResult(ok=False, output=f"不支持的 action: {action}")

    def _resolve_out(self, rel: str | None) -> Path:
        if rel:
            path = (self.root / rel).resolve()
        else:
            stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
            path = (self.root / self.output_dir / f"shot_{stamp}.png").resolve()
        path.relative_to(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _capture(self, kwargs: dict[str, Any]) -> SkillResult:
        try:
            out = self._resolve_out(str(kwargs.get("path") or "").strip() or None)
        except ValueError:
            return SkillResult(ok=False, output="保存路径越界")

        # 1) mss + Pillow
        try:
            import mss  # type: ignore
            from PIL import Image  # type: ignore

            monitor = int(kwargs.get("monitor") or 1)
            with mss.mss() as sct:
                monitors = sct.monitors
                idx = monitor if 0 <= monitor < len(monitors) else 1
                shot = sct.grab(monitors[idx])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(out)
            logger.notice(f"screenshot captured: {out}")
            return SkillResult(
                ok=True,
                output=f"已截屏保存: {out.relative_to(self.root).as_posix()} ({shot.width}x{shot.height})",
                data={
                    "path": out.relative_to(self.root).as_posix(),
                    "width": shot.width,
                    "height": shot.height,
                },
            )
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            return SkillResult(ok=False, output=f"截屏失败: {exc}")

        # 2) Windows PowerShell 兜底
        import os
        import subprocess

        if os.name == "nt":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Add-Type -AssemblyName System.Drawing; "
                "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
                "$g=[System.Drawing.Graphics]::FromImage($bmp); "
                "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); "
                f"$bmp.Save('{str(out).replace(chr(39), chr(39)+chr(39))}'); "
                "$g.Dispose(); $bmp.Dispose();"
            )
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return SkillResult(ok=False, output=f"PowerShell 截屏失败: {exc}")
            if proc.returncode != 0 or not out.is_file():
                return SkillResult(
                    ok=False,
                    output="截屏失败。可安装: pip install mss pillow",
                )
            meta = self._file_meta(out, include_b64=False)
            return SkillResult(
                ok=True,
                output=f"已截屏保存: {out.relative_to(self.root).as_posix()}",
                data=meta,
            )

        return SkillResult(
            ok=False,
            output="当前环境无法截屏。请安装: pip install mss pillow",
        )

    def _read(self, kwargs: dict[str, Any]) -> SkillResult:
        rel = str(kwargs.get("path") or "").strip()
        if not rel:
            return SkillResult(ok=False, output="read 需要 path")
        path = (self.root / rel).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return SkillResult(ok=False, output=f"path 越界: {rel}")
        if not path.is_file():
            return SkillResult(ok=False, output=f"文件不存在: {rel}")
        include = bool(kwargs.get("include_base64"))
        max_b64 = int(kwargs.get("max_base64_bytes") or 200000)
        meta = self._file_meta(path, include_b64=include, max_b64=max_b64)
        size = f"{meta.get('width')}x{meta.get('height')}" if meta.get("width") else "未知尺寸"
        return SkillResult(
            ok=True,
            output=f"图片 {rel} · {size} · {meta.get('bytes')} bytes · {meta.get('format')}",
            data=meta,
        )

    def _file_meta(
        self,
        path: Path,
        *,
        include_b64: bool,
        max_b64: int = 200000,
    ) -> dict[str, Any]:
        data = path.read_bytes()
        fmt = path.suffix.lower().lstrip(".") or "bin"
        wh = _png_size(data) or _jpeg_size(data)
        try:
            rel_path = path.relative_to(self.root).as_posix()
        except ValueError:
            rel_path = str(path)
        meta: dict[str, Any] = {
            "path": rel_path,
            "bytes": len(data),
            "format": fmt,
        }
        if wh:
            meta["width"], meta["height"] = wh
        if include_b64 and len(data) <= max_b64:
            meta["base64"] = base64.b64encode(data).decode("ascii")
        return meta
