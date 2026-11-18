from app.tools.base import ErrorCode, ToolMeta, ToolResult


def meta() -> ToolMeta:
    return ToolMeta(tool="dummy", source="test")


def test_success_has_data_and_no_error():
    result = ToolResult.success({"count": 2}, meta())

    assert result.ok is True
    assert result.data == {"count": 2}
    assert result.error is None


def test_failure_has_error_and_no_data():
    result = ToolResult.failure(ErrorCode.NO_RESULT, "결과가 없습니다", meta())

    assert result.ok is False
    assert result.data is None
    assert result.error.code is ErrorCode.NO_RESULT
    assert result.error.message == "결과가 없습니다"


def test_retryable_is_decided_by_code():
    assert ToolResult.failure(ErrorCode.TIMEOUT, "", meta()).error.retryable is True
    assert ToolResult.failure(ErrorCode.UPSTREAM_ERROR, "", meta()).error.retryable is True
    assert ToolResult.failure(ErrorCode.NO_RESULT, "", meta()).error.retryable is False


def test_fatal_codes_are_not_worth_replanning():
    for code in (ErrorCode.MISSING_API_KEY, ErrorCode.AUTH_FAILED, ErrorCode.QUOTA_EXCEEDED):
        assert ToolResult.failure(code, "", meta()).error.is_fatal is True
    assert ToolResult.failure(ErrorCode.NO_RESULT, "", meta()).error.is_fatal is False


def test_serializes_to_plain_json():
    result = ToolResult.failure(ErrorCode.NO_ROUTE, "경로 없음", meta())
    dumped = result.model_dump(mode="json")

    assert dumped["ok"] is False
    assert dumped["error"]["code"] == "NO_ROUTE"
    assert dumped["meta"]["tool"] == "dummy"
