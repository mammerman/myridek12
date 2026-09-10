"""
Tests for student_tracker.py - ported logic must match the Node bridge's tested behavior.

Fixtures are the same ones used in the original bridge's test/student-tracker.test.js.
"""

from __future__ import annotations

from custom_components.myride_k12.student_tracker import (
    normalize_student,
    stop_time_to_minutes,
)

AM_RUN = {
    "runId": 1,
    "busNumber": "BUS 012",
    "activeVehicle": "BUS 057",
    "stopsInfo": [
        {"stopTime": "1900-01-01T08:45:00"},
        {"stopTime": "1900-01-01T09:10:00"},
    ],
}
PM_RUN = {
    "runId": 2,
    "busNumber": "BUS 012",
    "activeVehicle": "BUS 012",
    "stopsInfo": [
        {"stopTime": "1900-01-01T15:15:00"},
        {"stopTime": "1900-01-01T15:45:00"},
    ],
}
STUDENT_RAW = {
    "uniqueId": 2008416,
    "firstName": "Lucas",
    "lastName": "Gregory",
    "runInfo": [AM_RUN, PM_RUN],
}


def test_stop_time_to_minutes():
    assert stop_time_to_minutes("1900-01-01T09:02:22.99") == 9 * 60 + 2
    assert stop_time_to_minutes("1900-01-01T15:25:00") == 15 * 60 + 25
    assert stop_time_to_minutes(None) is None
    assert stop_time_to_minutes("garbage") is None


def test_during_am_window_picks_substitute_bus():
    snap = normalize_student(STUDENT_RAW, now_minutes=8 * 60 + 50)
    assert snap.bus == "BUS 057"
    assert snap.is_substitute is True
    assert snap.first_name == "Lucas"
    assert len(snap.todays_runs) == 2


def test_during_pm_window_picks_regular_bus():
    snap = normalize_student(STUDENT_RAW, now_minutes=15 * 60 + 20)
    assert snap.bus == "BUS 012"
    assert snap.is_substitute is False


def test_between_windows_picks_next_upcoming_run():
    snap = normalize_student(STUDENT_RAW, now_minutes=12 * 60)
    assert snap.bus == "BUS 012"  # PM run, since it's the next upcoming


def test_after_all_windows_falls_back_to_most_recent_past_run():
    snap = normalize_student(STUDENT_RAW, now_minutes=23 * 60)
    assert snap.bus == "BUS 012"


def test_empty_run_info_has_no_current_run():
    snap = normalize_student(
        {"uniqueId": "1", "firstName": "A", "lastName": "B", "runInfo": []},
        now_minutes=0,
    )
    assert snap.current_run is None
    assert snap.bus is None


def test_single_run_returned_regardless_of_time():
    single = {"uniqueId": "9", "firstName": "X", "runInfo": [AM_RUN]}
    snap = normalize_student(single, now_minutes=22 * 60)
    assert snap.bus == "BUS 057"


def test_uses_ha_configured_timezone_by_default(hass):
    """With no explicit now_minutes, normalize_student should use dt_util.now() (HA's own tz)."""
    from unittest.mock import patch

    import homeassistant.util.dt as dt_util

    fake_now = dt_util.now().replace(hour=8, minute=50)
    with patch(
        "custom_components.myride_k12.student_tracker.dt_util.now",
        return_value=fake_now,
    ):
        snap = normalize_student(STUDENT_RAW)
    assert snap.bus == "BUS 057"  # 8:50 falls in the AM window
