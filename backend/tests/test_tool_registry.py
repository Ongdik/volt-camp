import pytest
from pydantic import BaseModel

from app.tools.base import ErrorCode, Tool, ToolMeta
from app.tools.registry import ToolRegistry


class Empty(BaseModel):
    pass


class ExternalTool(Tool):
    name = "external_one"
    description = "외부 API 테스트용"
    input_model = Empty

    def run(self, args, meta: ToolMeta) -> dict:
        return {"from": "external"}


class LocalTool(Tool):
    name = "local_one"
    description = "로컬 서버 기능 테스트용"
    input_model = Empty
    kind = "local"

    def run(self, args, meta: ToolMeta) -> dict:
        return {"from": "local"}


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry([ExternalTool(), LocalTool()])


def test_register_and_lookup(registry):
    assert len(registry) == 2
    assert "external_one" in registry
    assert registry.names() == ["external_one", "local_one"]
    assert isinstance(registry.get("local_one"), LocalTool)
    assert registry.get("none") is None


def test_duplicate_name_is_rejected(registry):
    with pytest.raises(ValueError):
        registry.register(ExternalTool())


def test_invoke_by_name(registry):
    result = registry.invoke("external_one")

    assert result.ok is True
    assert result.data == {"from": "external"}


def test_unknown_tool_returns_failure_not_exception(registry):
    result = registry.invoke("made_up_tool", {"x": 1})

    assert result.ok is False
    assert result.error.code is ErrorCode.INVALID_INPUT
    assert result.meta.tool == "made_up_tool"


def test_specs_can_be_filtered_by_kind(registry):
    assert [s["function"]["name"] for s in registry.specs()] == ["external_one", "local_one"]
    assert [s["function"]["name"] for s in registry.specs(kind="external")] == ["external_one"]
    assert [s["function"]["name"] for s in registry.specs(kind="local")] == ["local_one"]
