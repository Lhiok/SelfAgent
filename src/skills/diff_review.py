"""对 git diff / 文本 diff 做静态复盘摘要。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.diff_review")

_RISK_PATTERNS = [
    (re.compile(r"(?i)password\s*="), "疑似硬编码密码"),
    (re.compile(r"(?i)api[_-]?key\s*="), "疑似硬编码 API Key"),
    (re.compile(r"(?i)secret\s*="), "疑似硬编码 Secret"),
    (re.compile(r"(?i)TODO|FIXME|HACK"), "残留 TODO/FIXME/HACK"),
    (re.compile(r"(?i)catch\s*\(\s*Exception"), "宽泛 Exception 捕获"),
    (re.compile(r"\beval\s*\("), "使用 eval"),
    (re.compile(r"(?i)ignore\s+ssl|verify\s*=\s*False"), "可能关闭 TLS 校验"),
]


class DiffReviewSkill(Skill):
    name = "diff_review"
    description = (
        "复盘代码改动：可读取 git diff、文件路径或直接传入 diff 文本，"
        "输出文件统计、增删行与风险关键词提示（静态规则，不调用模型）。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["review"],
                "default": "review",
            },
            "source": {
                "type": "string",
                "enum": ["git", "text", "path"],
                "default": "git",
                "description": "git=工作区 diff；text=用 diff 字段；path=读文件内容当补丁",
            },
            "diff": {"type": "string", "description": "source=text 时的 diff 正文"},
            "path": {"type": "string", "description": "source=path 时相对 root 的补丁文件"},
            "staged": {
                "type": "boolean",
                "default": False,
                "description": "source=git 时是否看已暂存 diff",
            },
        },
        "required": [],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        git_bin: str = "git",
        max_diff_chars: int = 200000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.git_bin = git_bin or "git"
        self.max_diff_chars = max_diff_chars
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="diff_review 已在配置中禁用")
        action = str(kwargs.get("action") or "review").strip().lower()
        if action != "review":
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        source = str(kwargs.get("source") or "git").strip().lower()
        if source == "git":
            diff_text, err = self._git_diff(bool(kwargs.get("staged")))
            if err:
                return SkillResult(ok=False, output=err)
        elif source == "text":
            diff_text = str(kwargs.get("diff") or "")
        elif source == "path":
            rel = str(kwargs.get("path") or "").strip()
            if not rel:
                return SkillResult(ok=False, output="path 不能为空")
            target = (self.root / rel).resolve()
            try:
                target.relative_to(self.root)
            except ValueError:
                return SkillResult(ok=False, output=f"path 越界: {rel}")
            if not target.is_file():
                return SkillResult(ok=False, output=f"文件不存在: {rel}")
            diff_text = target.read_text(encoding="utf-8", errors="replace")
        else:
            return SkillResult(ok=False, output=f"不支持的 source: {source}")

        if len(diff_text) > self.max_diff_chars:
            diff_text = diff_text[: self.max_diff_chars] + "\n…(截断)"

        report = self._analyze(diff_text)
        logger.notice(
            f"diff_review: files={report['file_count']} +{report['added']}/-{report['deleted']} risks={len(report['risks'])}"
        )
        return SkillResult(ok=True, output=report["text"], data=report)

    def _git_diff(self, staged: bool) -> tuple[str, str | None]:
        args = [self.git_bin, "diff"]
        if staged:
            args.append("--cached")
        try:
            proc = subprocess.run(
                args,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", f"git diff 失败: {exc}"
        if proc.returncode not in {0, 1}:
            return "", f"git diff 失败: {(proc.stderr or proc.stdout or '').strip()}"
        text = proc.stdout or ""
        if not text.strip():
            return "", "当前没有可复盘的 diff（工作区干净）"
        return text, None

    def _analyze(self, diff_text: str) -> dict[str, Any]:
        files: list[str] = []
        added = deleted = 0
        risks: list[dict[str, str]] = []
        current = ""
        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                parts = line.split(" b/", 1)
                current = parts[1] if len(parts) == 2 else line[11:]
                if current and current not in files:
                    files.append(current)
            elif line.startswith("+++ b/"):
                current = line[6:]
                if current not in files:
                    files.append(current)
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
                for pat, label in _RISK_PATTERNS:
                    if pat.search(line):
                        risks.append({"file": current or "?", "label": label, "line": line[1:].strip()[:160]})
                        break
            elif line.startswith("-") and not line.startswith("---"):
                deleted += 1

        lines = [
            "=== Diff 复盘 ===",
            f"文件数: {len(files)} · 新增行: {added} · 删除行: {deleted}",
        ]
        if files:
            lines.append("涉及文件:")
            for f in files[:40]:
                lines.append(f"  - {f}")
            if len(files) > 40:
                lines.append(f"  …另有 {len(files) - 40} 个文件")
        if risks:
            lines.append("风险提示:")
            for r in risks[:30]:
                lines.append(f"  - [{r['label']}] {r['file']}: {r['line']}")
            if len(risks) > 30:
                lines.append(f"  …另有 {len(risks) - 30} 条")
        else:
            lines.append("风险提示: 未命中内置规则（不代表无问题）")
        lines.append("建议: 重点人工确认公共 API、空引用路径与权限边界。")
        return {
            "file_count": len(files),
            "files": files,
            "added": added,
            "deleted": deleted,
            "risks": risks,
            "text": "\n".join(lines),
        }
