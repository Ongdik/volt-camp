"""Tool 레지스트리: 이름으로 등록 · 조회 · 실행하고, LLM에 넘길 명세 목록을 만든다."""

from __future__ import annotations

from typing import Any, Iterable, Literal

from app.tools.base import ErrorCode, Tool, ToolMeta, ToolResult


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"이미 등록된 Tool 이름입니다: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, kind: Literal["external", "local"] | None = None) -> list[dict[str, Any]]:
        """Agent(LLM)에는 이름 · 설명 · 입력 스키마만 넘긴다."""
        return [t.spec() for t in self._tools.values() if kind is None or t.kind == kind]

    def invoke(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            # LLM이 없는 Tool 이름을 지어낸 경우에도 예외 대신 실패 응답
            meta = ToolMeta(tool=name, requested=dict(args or {}))
            return ToolResult.failure(ErrorCode.INVALID_INPUT, f"등록되지 않은 도구입니다: {name}", meta)
        return tool.invoke(args)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
