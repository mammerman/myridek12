"""Device tracker platform for MyRide K-12 - one per student, following today's bus."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity

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
    """Set up device trackers, one per student, including any discovered later."""
    async_setup_student_entities(
        entry.runtime_data,
        async_add_entities,
        lambda coordinator, student_id: [MyRideTracker(coordinator, student_id)],
    )


class MyRideTracker(MyRideStudentEntity, TrackerEntity):
    """A student's location, following whichever bus is assigned to them today."""

    _attr_translation_key = "bus"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: MyRideCoordinator, student_id: str) -> None:
        """Set up the tracker."""
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{student_id}_tracker"

    @property
    def latitude(self) -> float | None:
        """Return latitude, or None while we haven't heard from the bus yet today."""
        return self.snapshot.latitude if self.snapshot else None

    @property
    def longitude(self) -> float | None:
        """Return longitude, or None while we haven't heard from the bus yet today."""
        return self.snapshot.longitude if self.snapshot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extra context: bus, whether it's a substitute, and how fresh the fix is."""
        snap = self.snapshot
        if snap is None:
            return {}
        attrs: dict[str, Any] = {
            "bus": snap.bus,
            "is_substitute": snap.is_substitute,
            "heading": snap.heading,
            "speed": snap.speed,
            "log_time": snap.log_time,
            "live": self.coordinator.is_live(snap),
        }
        if snap.log_time_changed_at is not None:
            elapsed = self.coordinator.hass.loop.time() - snap.log_time_changed_at
            attrs["seconds_since_last_fix"] = round(elapsed)
        return attrs
