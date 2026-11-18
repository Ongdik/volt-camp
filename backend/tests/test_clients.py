import pytest
import requests

from app.clients import data_go_kr, http, kakao
from app.config import get_settings
from app.geo import haversine_km
from app.tools.base import ErrorCode, ToolFailure
from tests.conftest import FakeResponse, load_fixture

GATEWAY_ERROR_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader>
<errMsg>SERVICE ERROR</errMsg><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""


def code_of(fn, *args, **kwargs) -> ErrorCode:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


# ---------- http

def test_http_timeout_and_connection_errors(fake_get):
    fake_get(requests.Timeout())
    assert code_of(http.get, "https://x", source="t") is ErrorCode.TIMEOUT
    fake_get(requests.ConnectionError())
    assert code_of(http.get, "https://x", source="t") is ErrorCode.UPSTREAM_ERROR


@pytest.mark.parametrize("status, expected", [
    (401, ErrorCode.AUTH_FAILED), (403, ErrorCode.AUTH_FAILED),
    (429, ErrorCode.QUOTA_EXCEEDED), (502, ErrorCode.UPSTREAM_ERROR),
])
def test_http_status_codes(fake_get, status, expected):
    fake_get(FakeResponse({}, status_code=status))
    assert code_of(http.get, "https://x", source="t") is expected


def test_parse_body_reads_xml_and_rejects_garbage():
    assert "OpenAPI_ServiceResponse" in http.parse_body(FakeResponse(text=GATEWAY_ERROR_XML))
    assert code_of(http.parse_body, FakeResponse(text="<<not xml")) is ErrorCode.PARSE_ERROR


# ---------- 공공데이터포털

def test_data_go_kr_items_from_real_response(fake_get):
    calls = fake_get(FakeResponse(load_fixture("gocamping_location.json")))
    body = data_go_kr.request("https://apis.data.go.kr/x", {"pageNo": 1}, source="고캠핑")

    assert len(data_go_kr.items(body)) == 10
    assert data_go_kr.total_count(body) == 216
    assert calls[0]["params"]["serviceKey"] == "test-data-key"


def test_data_go_kr_empty_items_is_empty_list(fake_get):
    fake_get(FakeResponse(load_fixture("gocamping_empty.json")))
    body = data_go_kr.request("https://x", {}, source="고캠핑")
    assert data_go_kr.items(body) == []


def test_data_go_kr_single_item_dict_becomes_list():
    body = {"response": {"body": {"items": {"item": {"a": 1}}}}}
    assert data_go_kr.items(body) == [{"a": 1}]


def test_data_go_kr_gateway_error_in_http_200(fake_get):
    fake_get(FakeResponse(text=GATEWAY_ERROR_XML))
    assert code_of(data_go_kr.request, "https://x", {}, source="t") is ErrorCode.AUTH_FAILED


def test_data_go_kr_result_code_error(fake_get):
    fake_get(FakeResponse(load_fixture("kma_out_of_range.json")))
    with pytest.raises(ToolFailure) as exc:
        data_go_kr.request("https://x", {}, source="기상청")
    assert exc.value.code is ErrorCode.UPSTREAM_ERROR
    assert "최근 3일" in exc.value.message


def test_data_go_kr_no_data_code(fake_get):
    fake_get(FakeResponse({"response": {"header": {"resultCode": "03", "resultMsg": "NO_DATA"}}}))
    assert code_of(data_go_kr.request, "https://x", {}, source="t") is ErrorCode.NO_RESULT


def test_data_go_kr_missing_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "")
    get_settings.cache_clear()
    assert code_of(data_go_kr.request, "https://x", {}, source="t") is ErrorCode.MISSING_API_KEY


# ---------- 카카오

def test_geocode_address(fake_get):
    calls = fake_get(FakeResponse(load_fixture("kakao_address.json")))
    place = kakao.geocode("서울특별시 중구 세종대로 110")

    assert round(place.lat, 3) == 37.566 and round(place.lon, 3) == 126.978
    assert calls[0]["headers"]["Authorization"] == "KakaoAK test-kakao-key"


def test_geocode_falls_back_to_keyword(fake_get):
    keyword = {"documents": [{"place_name": "가평군청", "road_address_name": "경기 가평군 가평읍 석봉로 181",
                              "x": "127.5097", "y": "37.8315"}]}
    fake_get(lambda url, params: FakeResponse(load_fixture("kakao_empty.json") if "address" in url else keyword))
    place = kakao.geocode("가평")

    assert place.name == "가평군청" and place.lat == 37.8315


def test_geocode_nothing_found(fake_get):
    fake_get(FakeResponse(load_fixture("kakao_empty.json")))
    assert code_of(kakao.geocode, "없는곳") is ErrorCode.NO_RESULT


def test_region_code_prefers_legal_dong(fake_get):
    fake_get(FakeResponse({"documents": [
        {"region_type": "B", "code": "4182025000"}, {"region_type": "H", "code": "4182025099"}]}))
    assert kakao.region_code(37.83, 127.51) == "4182025000"


def test_kakao_missing_key(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    get_settings.cache_clear()
    assert code_of(kakao.geocode, "가평") is ErrorCode.MISSING_API_KEY


# ---------- 거리

def test_haversine_seoul_to_gapyeong():
    # 서울시청 → 가평군청 직선거리 약 55km
    assert 54 < haversine_km(37.5663, 126.9779, 37.8315, 127.5097) < 57
