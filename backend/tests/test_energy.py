from datetime import datetime

import pytest

from app.agent.devices import Vehicle, make_device
from app.agent.energy import (charge_minutes, evaluate, latest_charger_arrival,
                              reduce_hours_suggestion, temp_factor)

ONE_WAY_KM = 74.7                      # 서울시청 → 가평 (길찾기 실제 응답)
MILD = {"night_min_temp_c": 5.0, "max_precip_prob": 20, "rain_hours": []}
RAINY = {"night_min_temp_c": 5.0, "max_precip_prob": 80, "rain_hours": ["2026-11-19T21:00"]}


def car(soc, min_return=0.30):
    return Vehicle(capacity_kwh=77.4, soc_start=soc, efficiency_km_per_kwh=5.0, min_return=min_return)


@pytest.mark.parametrize("t, f", [(15, 1.0), (10, 1.0), (5, 1.15), (0, 1.15), (-3, 1.30), (-10, 1.30), (-15, 1.40), (None, 1.0)])
def test_temp_factor(t, f):
    assert temp_factor(t) == f


def test_ok_case_matches_hand_calculation():
    mat = make_device("전기장판", hours=8)
    ev = evaluate(car(0.80), [mat], ONE_WAY_KM, MILD)

    assert ev.status == "OK" and ev.failed == []
    # 주행 74.7 / 5 × 1.15 = 17.18 kWh, 장판 200W × 8h / 0.9 = 1.78 kWh
    assert ev.metrics["e_drive_oneway_kwh"] == pytest.approx(17.18, abs=0.01)
    assert ev.metrics["e_camp_kwh"] == pytest.approx(1.78, abs=0.01)
    soc = ev.metrics["soc"]
    assert (soc["arrive"], soc["after_camp"], soc["return"]) == pytest.approx((0.578, 0.555, 0.333), abs=0.001)


def test_energy_shortage_and_deficit():
    devices = [make_device("전기히터", hours=6), make_device("전기장판", hours=8)]
    ev = evaluate(car(0.45), devices, ONE_WAY_KM, MILD)

    assert ev.status == "FAIL" and "ENERGY" in ev.failed
    assert ev.metrics["soc"]["after_camp"] == pytest.approx(0.119, abs=0.001)
    assert ev.deficit_kwh == 31.2        # 귀가 목표 30%까지 모자란 양
    assert any("V2L 방전 제한" in r for r in ev.reasons)
    assert any("귀가 잔량" in r for r in ev.reasons)


def test_charge_stop_restores_energy():
    devices = [make_device("전기히터", hours=6), make_device("전기장판", hours=8)]
    ev = evaluate(car(0.45), devices, ONE_WAY_KM, MILD, charge_kwh=31.2)
    assert "ENERGY" not in ev.failed


def test_electric_site_uses_grid_power():
    devices = [make_device("전기히터", hours=6), make_device("전기장판", hours=8)]
    ev = evaluate(car(0.45, min_return=0.1), devices, ONE_WAY_KM, RAINY, has_electric=True)

    assert ev.metrics["e_camp_kwh"] == 0 and ev.metrics["e_camp_from_site_kwh"] > 0
    assert "RAIN" not in ev.failed and "POWER" not in ev.failed


def test_rain_blocks_outdoor_and_heating_devices():
    ev = evaluate(car(0.80, min_return=0.1), [make_device("전기히터", hours=2)], ONE_WAY_KM, RAINY)
    assert ev.failed == ["RAIN"]
    assert "전기히터" in ev.reasons[0]


def test_rain_is_fine_for_indoor_only_devices():
    ev = evaluate(car(0.80), [make_device("전기장판", hours=8)], ONE_WAY_KM, RAINY)
    assert ev.status == "OK"
    assert ev.metrics["rain_expected"] is True


def test_power_limit():
    devices = [make_device("전기히터", 1), make_device("전기포트", 0.2), make_device("인덕션", 0.5)]
    ev = evaluate(car(0.9, min_return=0.1), devices, 10, MILD)
    assert ev.failed == ["POWER"]
    assert ev.metrics["total_power_w"] == 4000


def test_no_forecast_is_unjudged_not_invented():
    ev = evaluate(car(0.80), [make_device("전기히터", 2)], ONE_WAY_KM, None)
    assert ev.unjudged == ["RAIN"] and "RAIN" not in ev.failed
    assert ev.metrics["temp_factor"] == 1.0
    assert ev.assumptions


def test_charge_minutes_and_latest_arrival():
    assert charge_minutes(31.2, 100) == 25          # 31.2 / (100 × 0.85) × 60 × 1.1 = 24.2 → 25분
    assert charge_minutes(0, 100) == 0
    latest = latest_charger_arrival(datetime(2026, 11, 19, 18, 0), 25, 20)
    assert latest == datetime(2026, 11, 19, 17, 15)


def test_reduce_hours_suggestion():
    devices = [make_device("전기히터", hours=6), make_device("전기장판", hours=8)]
    s = reduce_hours_suggestion(devices, 1.0)       # 히터 1시간 = 1.11kWh
    assert s == {"device": "전기히터", "from_hours": 6, "to_hours": 5.0}
    assert reduce_hours_suggestion(devices, 100) is None   # 줄여도 못 메움


def test_device_catalog_and_user_values():
    assert make_device("전기 장판", 8).power_w == 200 and make_device("전기 장판", 8).assumed_power
    custom = make_device("전기장판", 8, power_w=120)
    assert custom.power_w == 120 and not custom.assumed_power
    assert make_device("전기포트", 0.2).heating
    with pytest.raises(ValueError):
        make_device("모르는기기", 1)


def test_detour_outbound_and_direct_return():
    mat = make_device("전기장판", hours=8)
    same = evaluate(car(0.80), [mat], ONE_WAY_KM, MILD)
    detour = evaluate(car(0.80), [mat], ONE_WAY_KM + 10, MILD, return_distance_km=ONE_WAY_KM, charge_after_km=40)

    assert detour.metrics["e_drive_back_kwh"] == same.metrics["e_drive_back_kwh"]
    assert detour.metrics["e_drive_oneway_kwh"] > same.metrics["e_drive_oneway_kwh"]
    # 충전소(40km) 도착 직전: 0.80 − 40 × 1.15 / 5 / 77.4 = 0.681
    assert detour.metrics["soc"]["at_charger"] == pytest.approx(0.681, abs=0.001)
