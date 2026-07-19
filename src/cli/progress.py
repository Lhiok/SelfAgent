"""CLI 进度渲染（Windows 友好）。"""

from __future__ import annotations

import sys
from typing import Any


class ProgressPrinter:
    def __init__(self, *, show_delta: bool = True) -> None:
        self.show_delta = show_delta
        self._delta_open = False
        self.last_step: int | None = None

    def close_delta(self) -> None:
        if self._delta_open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._delta_open = False

    def __call__(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "assistant_delta":
            if not self.show_delta:
                return
            delta = str(event.get("delta") or "")
            if not delta:
                return
            if not self._delta_open:
                sys.stdout.write("助手… ")
                self._delta_open = True
            sys.stdout.write(delta)
            sys.stdout.flush()
            return

        self.close_delta()

        if etype == "skill":
            status = event.get("status")
            skill = event.get("skill")
            if status == "start":
                print(f"  ▸ skill {skill} …", flush=True)
            elif status == "end":
                ok = event.get("ok")
                mark = "ok" if ok else "err"
                print(f"  ▸ skill {skill} [{mark}]", flush=True)
            return

        if etype == "cancelled":
            print(f"  ! {event.get('message') or '已取消'}", flush=True)
            return

        if etype == "step":
            idx = event.get("index")
            self.last_step = int(idx) if idx is not None else self.last_step
            thought = (event.get("thought") or "").strip()
            actions = event.get("actions") or []
            names = []
            for a in actions:
                if isinstance(a, dict):
                    names.append(str(a.get("action") or "?"))
                else:
                    names.append(str(a))
            line = f"  · step {idx}"
            if names:
                line += f" [{', '.join(names)}]"
            if thought:
                short = thought if len(thought) <= 80 else thought[:79] + "…"
                line += f" — {short}"
            print(line, flush=True)
            return

        if etype == "status":
            msg = event.get("message") or event.get("phase") or ""
            if msg:
                print(f"  … {msg}", flush=True)
