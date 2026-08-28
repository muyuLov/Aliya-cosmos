"""Task 4.2: Agency Window 三因素主体约束测试

验证 activityLoad / privacy / deviceAccess 三因素门控 + 容量矩阵。
"""

import pytest


def test_agency_create():
    """AgencyWindow 应持有三因素默认值"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow()
    assert aw.activity_load == 0.0
    assert aw.privacy is True
    assert aw.device_access is True


def test_agency_full_capacity():
    """三因素全绿时容量应为 1.0"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(activity_load=0.0, privacy=True, device_access=True)
    capacity = aw.get_capacity()
    assert capacity == pytest.approx(1.0)


def test_agency_blocked_by_privacy():
    """privacy=False 时容量应为 0"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(privacy=False)
    assert aw.get_capacity() == 0.0


def test_agency_blocked_by_device_access():
    """device_access=False 时容量应为 0"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(device_access=False)
    assert aw.get_capacity() == 0.0


def test_agency_high_load_reduces_capacity():
    """高 activity_load 应降低容量"""
    from agent.proactive.agency import AgencyWindow

    aw_low = AgencyWindow(activity_load=0.0)
    aw_high = AgencyWindow(activity_load=0.9)
    assert aw_high.get_capacity() < aw_low.get_capacity()


def test_agency_can_contact():
    """容量足够时 can_contact 应返回 True"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(activity_load=0.0, privacy=True, device_access=True)
    assert aw.can_contact() is True


def test_agency_cannot_contact_when_blocked():
    """容量为 0 时 can_contact 应返回 False"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(privacy=False)
    assert aw.can_contact() is False


def test_agency_capacity_matrix():
    """容量矩阵：activity_load 影响应为非线性"""
    from agent.proactive.agency import AgencyWindow

    aw_0 = AgencyWindow(activity_load=0.0)
    aw_05 = AgencyWindow(activity_load=0.5)
    aw_1 = AgencyWindow(activity_load=1.0)

    cap_0 = aw_0.get_capacity()
    cap_05 = aw_05.get_capacity()
    cap_1 = aw_1.get_capacity()

    assert cap_0 > cap_05 > cap_1
    assert cap_1 == 0.0  # 完全忙时应为 0


def test_agency_validate_candidate():
    """validate_contact_candidate 应验证联系候选"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(activity_load=0.0, privacy=True, device_access=True)
    assert aw.validate_contact_candidate(motive="关心", target="user") is True


def test_agency_reject_empty_motive():
    """空 motive 应被拒绝"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(activity_load=0.0, privacy=True, device_access=True)
    assert aw.validate_contact_candidate(motive="", target="user") is False


def test_agency_to_dict():
    """to_dict 应返回三因素和容量"""
    from agent.proactive.agency import AgencyWindow

    aw = AgencyWindow(activity_load=0.5, privacy=True, device_access=True)
    d = aw.to_dict()
    assert "activity_load" in d
    assert "privacy" in d
    assert "device_access" in d
    assert "capacity" in d
