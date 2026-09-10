"""
Coordinator for MyRide K-12.

Two things run concurrently:
  - a normal DataUpdateCoordinator poll of /api/student every STUDENT_POLL_INTERVAL,
    which tells us which vehicle each student is on today (regular or substitute)
  - an always-on background task holding the SignalR stream open, pushing a data
    update every time one of *our* buses reports a new location

Both feed the same `self.data: dict[str, StudentSnapshot]`. Reconnection uses the
same negotiate-fresh-each-time approach the spike validated, with backoff that
resets once a connection has proven stable for a while.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MyRideApiClient, MyRideAuthError, MyRideConnectionError
from .const import (
    CONF_REFRESH_TOKEN,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    LIVE_RECHECK_INTERVAL,
    LOGGER,
    MAX_BACKOFF,
    RECONNECT_RESET_AFTER,
    STUDENT_POLL_INTERVAL,
)
from .student_tracker import StudentSnapshot, normalize_student

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import MyRideConfigEntry


class MyRideCoordinator(DataUpdateCoordinator[dict[str, StudentSnapshot]]):
    """Owns the API client, the roster poll, and the live stream."""

    config_entry: MyRideConfigEntry

    def __init__(self, hass: HomeAssistant, entry: MyRideConfigEntry) -> None:
        """Set up the coordinator; does not connect to anything yet."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=STUDENT_POLL_INTERVAL,
        )
        self.api = MyRideApiClient(
            async_get_clientsession(hass), entry.data[CONF_REFRESH_TOKEN]
        )
        self.api.tenant_id = entry.data.get("tenant_id")
        self._active_bus_to_student: dict[str, str] = {}
        self._backoff = 1.0
        entry.async_on_unload(
            async_track_time_interval(
                hass, self._async_recheck_staleness, LIVE_RECHECK_INTERVAL
            )
        )

    def async_start_stream(self) -> None:
        """Start the always-on live-location stream as a background task."""
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_stream_loop(),
            name=f"{DOMAIN} stream {self.config_entry.entry_id}",
        )

    def is_live(self, snapshot: StudentSnapshot) -> bool:
        """
        Whether a student's last reported position looks fresh vs. a stale heartbeat.

        See const.DEFAULT_STALE_AFTER for why this can't just check speed > 0 or
        "did a message arrive" - the hub keeps re-sending the same stale fix.
        """
        if snapshot.log_time_changed_at is None:
            return False
        return (
            self.hass.loop.time() - snapshot.log_time_changed_at
        ) < DEFAULT_STALE_AFTER.total_seconds()

    # ── Roster polling (built-in scheduling + auth-failure handling) ──

    async def _async_update_data(self) -> dict[str, StudentSnapshot]:
        try:
            raw_students = await self.api.async_get_students()
        except MyRideAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MyRideConnectionError as err:
            raise UpdateFailed(str(err)) from err

        self._persist_rotated_token_if_needed()

        data = dict(self.data or {})
        active_map: dict[str, str] = {}
        for raw in raw_students:
            new_snap = normalize_student(raw)
            previous = data.get(new_snap.unique_id)
            if previous is not None:
                # Keep whatever live position/timing data we already had; only the
                # roster fields (run/bus/name) come from this poll.
                new_snap = replace(
                    new_snap,
                    latitude=previous.latitude,
                    longitude=previous.longitude,
                    heading=previous.heading,
                    speed=previous.speed,
                    log_time=previous.log_time,
                    log_time_changed_at=previous.log_time_changed_at,
                )
            data[new_snap.unique_id] = new_snap
            if new_snap.bus:
                active_map[new_snap.bus] = new_snap.unique_id

        self._active_bus_to_student = active_map
        return data

    def _persist_rotated_token_if_needed(self) -> None:
        if self.api.refresh_token != self.config_entry.data.get(CONF_REFRESH_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_REFRESH_TOKEN: self.api.refresh_token,
                },
            )

    # ── Live stream (background task, independent of the polling schedule above) ──

    async def _async_stream_loop(self) -> None:
        first_attempt = True
        while True:
            started_at = self.hass.loop.time()
            if first_attempt:
                LOGGER.info("Connecting to the MyRide live location stream")
                first_attempt = False
            try:
                await self.api.async_watch(self._on_location, self._on_event)
            except MyRideAuthError as err:
                LOGGER.warning("MyRide refresh token rejected: %s", err)
                self.config_entry.async_start_reauth(self.hass)
                return
            except (MyRideConnectionError, TimeoutError) as err:
                # Deliberately INFO, not DEBUG: a connection that never succeeds
                # is exactly what produces "device_tracker with no lat/lon and
                # no error" - this needs to be visible without the user having
                # to turn on debug logging first to find out anything's wrong.
                LOGGER.info("MyRide stream disconnected (%s), reconnecting", err)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bug in the stream loop shouldn't kill HA's task forever
                LOGGER.exception("Unexpected error in MyRide stream loop")

            alive_for = self.hass.loop.time() - started_at
            self._backoff = (
                1.0
                if alive_for > RECONNECT_RESET_AFTER.total_seconds()
                else min(self._backoff * 2, MAX_BACKOFF.total_seconds())
            )
            await asyncio.sleep(self._backoff)

    def _on_location(self, loc: dict[str, Any]) -> None:
        bus = loc.get("assetUniqueId")
        student_id = self._active_bus_to_student.get(bus) if bus else None
        if student_id is None:
            # Debug, not silent: if this fires a lot, `bus` not matching any
            # value in _active_bus_to_student (e.g. a name-format mismatch
            # between /api/student's activeVehicle and the hub's
            # assetUniqueId) is exactly the kind of thing that would produce
            # "connected fine, but lat/lon never populate".
            LOGGER.debug(
                "Location for untracked bus %r ignored (tracking: %s)",
                bus,
                sorted(self._active_bus_to_student),
            )
            return
        current = (self.data or {}).get(student_id)
        if current is None:
            return

        log_time = loc.get("logTime")
        changed = log_time != current.log_time
        updated = replace(
            current,
            latitude=loc.get("latitude"),
            longitude=loc.get("longitude"),
            heading=loc.get("heading"),
            speed=loc.get("speed"),
            log_time=log_time,
            log_time_changed_at=self.hass.loop.time()
            if changed
            else current.log_time_changed_at,
        )
        new_data = dict(self.data or {})
        new_data[student_id] = updated
        self.async_set_updated_data(new_data)

    def _on_event(self, target: str, arguments: list[Any]) -> None:
        LOGGER.debug("Unhandled MyRide hub event %s: %s", target, arguments)

    @callback
    def _async_recheck_staleness(self, _now: Any) -> None:
        """
        Re-render entities periodically so a live->stale flip shows up.

        Runs even when no new message has arrived to trigger it otherwise.
        """
        if self.data:
            self.async_update_listeners()
