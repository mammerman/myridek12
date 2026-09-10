"""
Binary sensor platform for MyRide K-12.

`live` replaces the Node bridge's speed-based "moving" sensor - see
const.DEFAULT_STALE_AFTER for why speed alone isn't a reliable signal (a
stopped-but-live bus and a stale cached fix both report speed 0/unchanged).

There's no separate "credentials expired" sensor here, unlike the original
bridge: an expired refresh token now raises ConfigEntryAuthFailed from the
coordinator, which HA surfaces as its own native reauthentication flow/repair
instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)

from .entity import MyRideStudentEntity, async_setup_student_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MyRideCoordinator
    from .data import MyRideConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - required by HA's platform setup signature, unused here
    entry: MyRideConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors, two per student, including any discovered later."""

    def make_entities(
        coordinator: MyRideCoordinator, student_id: str
    ) -> list[BinarySensorEntity]:
        return [
            MyRideLiveSensor(coordinator, student_id),
            MyRideSubstituteSensor(coordinator, student_id),
        ]

    async_setup_student_entities(entry.runtime_data, async_add_entities, make_entities)


class MyRideLiveSensor(MyRideStudentEntity, BinarySensorEntity):
    """On when the last fix is recent, off when it's a stale cached heartbeat."""

    _attr_translation_key = "live"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: MyRideCoordinator, student_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{student_id}_live"

    @property
    def is_on(self) -> bool | None:
        """Return whether the bus's position looks live right now."""
        snap = self.snapshot
        return self.coordinator.is_live(snap) if snap else None


class MyRideSubstituteSensor(MyRideStudentEntity, BinarySensorEntity):
    """On when today's active bus differs from the student's regular bus."""

    _attr_translation_key = "substitute"

    def __init__(self, coordinator: MyRideCoordinator, student_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{student_id}_substitute"

    @property
    def is_on(self) -> bool | None:
        """Return whether today's bus is a substitute."""
        snap = self.snapshot
        return snap.is_substitute if snap else None
