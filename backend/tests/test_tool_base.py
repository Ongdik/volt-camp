import pytest
from pydantic import BaseModel, Field

from app.tools.base import ErrorCode, Tool, ToolFailure, ToolMeta


class EchoInput(BaseModel):
    text: str = Field(description="돌려줄 문장")
    times: int = Field(default=1, ge=1, le=3)


class EchoTool(Tool):
    name = "echo"
    description = "테스트용: 받은 문장을 반복해 돌려줄 때 쓴다."
    input_model = EchoInput
    source = "test"

    def run(self, args: EchoInput, meta: ToolMeta) -> dict:
        if args.text == "없음":
            raise ToolFailure(ErrorCode.NO_RESULT, "돌려줄 문장이 없습니다")
        if args.text == "버그":
            raise ZeroDivisionError("예상 못 한 오류")
        if args.times > 1:
            meta.notes.append("여러 번 반복했습니다")
        return {"echo": args.text * args.times}


@pytest.fixture
def tool() -> EchoTool:
    return EchoTool()


def test_success_returns_data_and_meta(tool):
    result = tool.invoke({"text": "안녕", "times": 2})

    assert result.ok is True
    assert result.data == {"echo": "안녕안녕"}
    assert result.meta.tool == "echo"
    assert result.meta.source == "test"
    assert result.meta.requested == {"text": "안녕", "times": 2}
    assert result.meta.notes == ["여러 번 반복했습니다"]
    assert result.meta.elapsed_ms >= 0


def test_missing_required_input_becomes_invalid_input(tool):
    result = tool.invoke({})

    assert result.ok is False
    assert result.error.code is ErrorCode.INVALID_INPUT
    assert "text" in result.error.message


def test_out_of_range_input_becomes_invalid_input(tool):
    result = tool.invoke({"text": "a", "times": 9})

    assert result.error.code is ErrorCode.INVALID_INPUT
    assert "times" in result.error.message


def test_expected_failure_keeps_its_code(tool):
    result = tool.invoke({"text": "없음"})

    assert result.ok is False
    assert result.error.code is ErrorCode.NO_RESULT
    assert result.error.message == "돌려줄 문장이 없습니다"


def test_unexpected_exception_does_not_escape(tool):
    result = tool.invoke({"text": "버그"})

    assert result.ok is False
    assert result.error.code is ErrorCode.INTERNAL_ERROR
    assert "ZeroDivision" not in result.error.message  # 내부 오류 상세는 노출하지 않는다


def test_spec_is_openai_function_format():
    spec = EchoTool.spec()

    assert spec["type"] == "function"
    assert spec["function"]["name"] == "echo"
    assert spec["function"]["description"].startswith("테스트용")
    params = spec["function"]["parameters"]
    assert params["required"] == ["text"]
    assert set(params["properties"]) == {"text", "times"}
