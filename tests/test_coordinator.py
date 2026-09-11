"""
Tests for coordinator.py's location handling and staleness detection.

These exercise coordinator._on_location() and is_live() directly, seeding
coordinator.data by hand, rather than going through a real config entry setup
+ live WebSocket - that's covered at the config-flow level in
test_config_flow.py; this file is about the actual bus-tracking logic.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myride_k12.const import (
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    DEFAULT_STALE_AFTER,
    DOMAIN,
)
from custom_components.myride_k12.coordinator import MyRideCoordinator
from custom_components.myride_k12.student_tracker import RunInfo, StudentSnapshot


@pytest.fixture
async def coordinator(hass):
    """A coordinator with one tracked student, not wired up to a real API or stream."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="tenant-abc123",
        data={CONF_REFRESH_TOKEN: "fake-token", CONF_TENANT_ID: "tenant-abc123"},
    )
    entry.add_to_hass(hass)
    coord = MyRideCoordinator(hass, entry)
    coord.data = {
        "2008416": StudentSnapshot(
            unique_id="2008416",
            first_name="Lucas",
            last_name="Gregory",
            current_run=RunInfo(
                run_id=1,
                bus_number="BUS 012",
                active_vehicle="BUS 057",
                is_substitute=True,
                window_start=8 * 60,
                window_end=9 * 60,
            ),
            todays_runs=[],
        )
    }
    coord._active_bus_to_student = {"BUS 057": {"2008416"}}
    yield coord
    # The coordinator registers a periodic staleness-recheck timer via
    # entry.async_on_unload(), which normally gets cancelled when the config
    # entry unloads. This test constructs the coordinator directly rather than
    # through a full entry setup/unload cycle, so nothing would ever fire that
    # callback - run it by hand or the timer leaks past the test.
    await entry._async_process_on_unload(hass)


def test_location_for_untracked_bus_is_ignored(coordinator):
    """A NewLocation event for a bus that isn't one of our students' today is dropped."""
    coordinator._on_location(
        {"assetUniqueId": "BUS 999", "latitude": 1.0, "longitude": 2.0, "logTime": "t1"}
    )
    assert coordinator.data["2008416"].latitude is None


def test_location_for_tracked_bus_updates_snapshot(coordinator):
    """A NewLocation event for a tracked bus updates that student's position."""
    coordinator._on_location(
        {
            "assetUniqueId": "BUS 057",
            "latitude": 40.6892,
            "longitude": -74.0445,
            "heading": 138,
            "speed": 26,
            "logTime": "t1",
        }
    )
    snap = coordinator.data["2008416"]
    assert snap.latitude == 40.6892
    assert snap.speed == 26
    assert snap.log_time == "t1"
    assert snap.log_time_changed_at is not None


def test_location_for_shared_bus_updates_every_rider(coordinator):
    """
    Siblings on the same route share a bus number.

    One NewLocation event for that bus must update ALL of them, not just
    whichever student happened to be stored last.
    """
    coordinator.data["3011927"] = StudentSnapshot(
        unique_id="3011927",
        first_name="Margaret",
        last_name="Gregory",
        current_run=RunInfo(
            run_id=1,
            bus_number="BUS 057",
            active_vehicle="BUS 057",
            is_substitute=False,
            window_start=8 * 60,
            window_end=9 * 60,
        ),
        todays_runs=[],
    )
    coordinator._active_bus_to_student = {"BUS 057": {"2008416", "3011927"}}

    coordinator._on_location(
        {
            "assetUniqueId": "BUS 057",
            "latitude": 40.6892,
            "longitude": -74.0445,
            "heading": 138,
            "speed": 26,
            "logTime": "t1",
        }
    )

    for student_id in ("2008416", "3011927"):
        snap = coordinator.data[student_id]
        assert snap.latitude == 40.6892
        assert snap.speed == 26
        assert snap.log_time == "t1"
        assert snap.log_time_changed_at is not None


async def test_roster_poll_accumulates_students_on_a_shared_bus(coordinator):
    """
    Regression test for the original bug.

    Two students whose roster entries both resolve to the same
    activeVehicle must BOTH end up in _active_bus_to_student[bus] - not
    have the second overwrite the first.
    """
    lucas_run = {
        "runId": 1,
        "busNumber": "BUS 057",
        "activeVehicle": "BUS 057",
        "stopsInfo": [{"stopTime": "1900-01-01T08:45:00"}],
    }
    margaret_run = {
        "runId": 2,
        "busNumber": "BUS 057",
        "activeVehicle": "BUS 057",
        "stopsInfo": [{"stopTime": "1900-01-01T08:47:00"}],
    }
    raw_students = [
        {
            "uniqueId": 2008416,
            "firstName": "Lucas",
            "lastName": "Gregory",
            "runInfo": [lucas_run],
        },
        {
            "uniqueId": 3011927,
            "firstName": "Margaret",
            "lastName": "Gregory",
            "runInfo": [margaret_run],
        },
    ]

    with patch.object(
        coordinator.api, "async_get_students", return_value=raw_students
    ):
        await coordinator._async_update_data()

    assert coordinator._active_bus_to_student["BUS 057"] == {"2008416", "3011927"}


def test_is_live_false_before_any_message(coordinator):
    """No message received yet -> not live (never was there a fresh fix to trust)."""
    assert coordinator.is_live(coordinator.data["2008416"]) is False


def test_is_live_true_right_after_a_fresh_message(coordinator):
    """A message just arrived with a new logTime -> live."""
    coordinator._on_location(
        {"assetUniqueId": "BUS 057", "latitude": 1.0, "longitude": 2.0, "logTime": "t1"}
    )
    assert coordinator.is_live(coordinator.data["2008416"]) is True


def test_repeated_stale_heartbeat_does_not_reset_the_live_clock(coordinator):
    """
    This is the core finding from the spike: the hub re-sends the SAME logTime

    repeatedly for a parked/idle bus. Each of those messages must NOT look like
    a fresh fix just because a message arrived - is_live should go False once
    DEFAULT_STALE_AFTER has elapsed since logTime last actually *changed*,
    even if messages keep arriving with the old logTime.
    """
    coordinator._on_location(
        {
            "assetUniqueId": "BUS 057",
            "latitude": 1.0,
            "longitude": 2.0,
            "logTime": "same-time",
        }
    )
    first_changed_at = coordinator.data["2008416"].log_time_changed_at

    # Time passes well beyond the staleness threshold...
    with patch.object(
        coordinator.hass.loop,
        "time",
        return_value=first_changed_at + DEFAULT_STALE_AFTER.total_seconds() + 5,
    ):
        # ...and another message arrives, but with the SAME logTime (the stale-heartbeat case).
        coordinator._on_location(
            {
                "assetUniqueId": "BUS 057",
                "latitude": 1.0,
                "longitude": 2.0,
                "logTime": "same-time",
            }
        )
        assert (
            coordinator.data["2008416"].log_time_changed_at == first_changed_at
        )  # unchanged
        assert (
            coordinator.is_live(coordinator.data["2008416"]) is False
        )  # correctly flagged stale


def test_fresh_logtime_resets_the_live_clock(coordinator):
    """Once logTime actually advances, live goes back to True even after a stale stretch."""
    coordinator._on_location(
        {"assetUniqueId": "BUS 057", "latitude": 1.0, "longitude": 2.0, "logTime": "t1"}
    )
    first_changed_at = coordinator.data["2008416"].log_time_changed_at

    with patch.object(
        coordinator.hass.loop,
        "time",
        return_value=first_changed_at + DEFAULT_STALE_AFTER.total_seconds() + 5,
    ):
        coordinator._on_location(
            {
                "assetUniqueId": "BUS 057",
                "latitude": 1.1,
                "longitude": 2.1,
                "logTime": "t2",
            }
        )
        assert coordinator.data["2008416"].log_time_changed_at != first_changed_at
        assert coordinator.is_live(coordinator.data["2008416"]) is True
