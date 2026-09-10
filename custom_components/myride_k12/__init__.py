"""
The MyRide K-12 integration: real-time school bus tracking via Cognito + SignalR.

Endpoints and the negotiate-then-watch flow are credited to
legrego/myride-ha-bridge (https://github.com/legrego/myride-ha-bridge, MIT),
which reverse engineered them first as a standalone MQTT bridge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .coordinator import MyRideCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import MyRideConfigEntry

PLATFORMS: list[Platform] = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: MyRideConfigEntry) -> bool:
    """Set up MyRide K-12 from a config entry."""
    coordinator = MyRideCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # First roster poll happens here so entities exist as soon as platforms load,
    # same as any polling integration; this also surfaces a bad/expired token as
    # a setup failure (or triggers reauth) before we ever try to open the stream.
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The live stream runs independently of the roster's polling schedule -
    # see coordinator.py for why these are separate.
    coordinator.async_start_stream()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyRideConfigEntry) -> bool:
    """
    Unload a config entry.

    The stream task and staleness timer are both tied to the entry via
    async_create_background_task/async_on_unload, so they stop automatically -
    nothing extra to clean up here.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
