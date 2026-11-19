"""대화 조건 슬롯 합치기: 여러 턴에 나눠 말한 값이 유지되는지."""

from app.agent.state import Analysis, Conditions, DeviceUse


def analysis(**kw):
    return Analysis(intent="PLAN_TRIP", intent_reason="테스트", **kw)


def test_device_values_split_across_turns_are_kept():
    c, _ = Conditions().merge(analysis(devices=[DeviceUse(name="전기난로", hours=4), DeviceUse(name="전기포트")]))
    c, changed = c.merge(analysis(devices=[DeviceUse(name="전기난로", power_w=1000),
                                           DeviceUse(name="전기포트", hours=0.33)]))
    by = {d.name: d for d in c.devices}
    assert by["전기난로"].hours == 4 and by["전기난로"].power_w == 1000
    assert by["전기포트"].hours == 0.33
    assert "devices" in changed
    assert c.missing_for_plan() == ["출발 날짜", "출발지", "캠핑할 지역", "배터리 용량(kWh)", "현재 배터리 잔량(%)"]


def test_unmentioned_devices_stay_and_new_ones_are_added():
    c, _ = Conditions().merge(analysis(devices=[DeviceUse(name="전기장판", hours=10)]))
    c, _ = c.merge(analysis(devices=[DeviceUse(name="전기포트", hours=0.5)]))
    assert [d.name for d in c.devices] == ["전기장판", "전기포트"]


def test_removed_devices_are_dropped():
    c, _ = Conditions().merge(analysis(devices=[DeviceUse(name="전기장판", hours=10), DeviceUse(name="전기포트", hours=0.5)]))
    c, changed = c.merge(analysis(removed_devices=["전기장판"]))
    assert [d.name for d in c.devices] == ["전기포트"]
    assert changed == ["devices"]


def test_no_device_mention_keeps_list_unchanged():
    c, _ = Conditions().merge(analysis(devices=[DeviceUse(name="전기장판", hours=10)]))
    c2, changed = c.merge(analysis(soc_start_pct=45))
    assert c2.devices == c.devices and changed == ["soc_start_pct"]
