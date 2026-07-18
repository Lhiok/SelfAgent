"""轻量浏览器自动化（Playwright，可选依赖）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from log import get_logger
from skills.base import Skill, SkillResult
from skills.http_util import host_is_private, truncate

logger = get_logger("skills.browser")


class BrowserSkill(Skill):
    name = "browser"
    description = (
        "用 Playwright 控制无头浏览器：打开页面、取文本、截图、点击、填表。"
        "需安装 playwright 并执行 playwright install chromium。"
        "默认禁止访问私网地址。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["goto", "content", "screenshot", "click", "fill", "evaluate"],
                "description": "browser 操作",
            },
            "url": {"type": "string", "description": "goto 目标 URL"},
            "selector": {"type": "string", "description": "click/fill 的 CSS 选择器"},
            "text": {"type": "string", "description": "fill 填入的文本"},
            "script": {"type": "string", "description": "evaluate 的 JS 表达式"},
            "path": {
                "type": "string",
                "description": "screenshot 保存相对路径",
            },
            "timeout_ms": {"type": "integer", "default": 30000},
            "headless": {"type": "boolean", "default": True},
            "allow_private": {"type": "boolean", "default": False},
            "max_chars": {"type": "integer", "default": 80000},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        enabled: bool = True,
        allow_private: bool = False,
        output_dir: str = "logs/browser",
        max_chars: int = 80000,
    ) -> None:
        self.root = Path(root).resolve()
        self.enabled = enabled
        self.allow_private = allow_private
        self.output_dir = output_dir
        self.max_chars = max_chars
        self._page = None
        self._browser = None
        self._playwright = None

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="browser 已在配置中禁用")
        action = str(kwargs.get("action") or "").strip().lower()
        if not action:
            return SkillResult(ok=False, output="缺少 action")
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            return SkillResult(
                ok=False,
                output=(
                    "未安装 playwright。请执行:\n"
                    "  pip install playwright\n"
                    "  playwright install chromium"
                ),
            )

        timeout_ms = int(kwargs.get("timeout_ms") or 30000)
        headless = bool(kwargs.get("headless", True))
        allow_private = bool(kwargs.get("allow_private", self.allow_private))
        max_chars = int(kwargs.get("max_chars") or self.max_chars)

        try:
            if self._page is None:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=headless)
                self._page = self._browser.new_page()
                self._page.set_default_timeout(timeout_ms)

            page = self._page

            if action == "goto":
                url = str(kwargs.get("url") or "").strip()
                if not url:
                    return SkillResult(ok=False, output="goto 需要 url")
                err = self._check_url(url, allow_private)
                if err:
                    return SkillResult(ok=False, output=err)
                page.goto(url, wait_until="domcontentloaded")
                title = page.title()
                return SkillResult(
                    ok=True,
                    output=f"已打开: {page.url}\n标题: {title}",
                    data={"url": page.url, "title": title},
                )

            if action == "content":
                text = page.inner_text("body")
                clipped = truncate(text, max_chars)
                truncated = len(text) > max_chars > 0
                note = "\n…(已截断)" if truncated else ""
                return SkillResult(
                    ok=True,
                    output=f"{clipped}{note}",
                    data={"url": page.url, "truncated": truncated, "chars": len(text)},
                )

            if action == "screenshot":
                rel = str(kwargs.get("path") or "").strip()
                if rel:
                    out = (self.root / rel).resolve()
                else:
                    out = (self.root / self.output_dir / "page.png").resolve()
                try:
                    out.relative_to(self.root)
                except ValueError:
                    return SkillResult(ok=False, output="截图路径越界")
                out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(out), full_page=True)
                rel_out = out.relative_to(self.root).as_posix()
                return SkillResult(
                    ok=True,
                    output=f"页面截图已保存: {rel_out}",
                    data={"path": rel_out, "url": page.url},
                )

            if action == "click":
                sel = str(kwargs.get("selector") or "").strip()
                if not sel:
                    return SkillResult(ok=False, output="click 需要 selector")
                page.click(sel)
                return SkillResult(ok=True, output=f"已点击: {sel}", data={"selector": sel})

            if action == "fill":
                sel = str(kwargs.get("selector") or "").strip()
                text = str(kwargs.get("text") or "")
                if not sel:
                    return SkillResult(ok=False, output="fill 需要 selector")
                page.fill(sel, text)
                return SkillResult(
                    ok=True,
                    output=f"已填写 {sel} ({len(text)} chars)",
                    data={"selector": sel},
                )

            if action == "evaluate":
                script = str(kwargs.get("script") or "").strip()
                if not script:
                    return SkillResult(ok=False, output="evaluate 需要 script")
                result = page.evaluate(script)
                text = truncate(str(result), max_chars)
                return SkillResult(
                    ok=True,
                    output=text,
                    data={"url": page.url},
                )

            return SkillResult(ok=False, output=f"不支持的 action: {action}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("browser action failed")
            return SkillResult(ok=False, output=f"browser 失败: {exc}")

    def _check_url(self, url: str, allow_private: bool) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return f"仅支持 http/https: {url}"
        host = parsed.hostname or ""
        if not allow_private and host_is_private(host):
            return f"禁止访问私网/本机地址: {host}"
        return None

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = None
        self._browser = None
        self._playwright = None
