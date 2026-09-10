"""Sensor platform for MyRide K-12 - speed, heading, and today's bus per student."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import DEGREE, UnitOfSpeed

from .entity import MyRideStudentEntity, async_setup_student_entities

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MyRideCoordinator
    from .data import MyRideConfigEntry
    from .student_tracker import StudentSnapshot


@dataclass(frozen=True, kw_only=True)
class MyRideSensorDescription(SensorEntityDescription):
    """Adds the value-extraction function to the standard sensor description."""

    value_fn: Callable[[StudentSnapshot], object]


SENSOR_DESCRIPTIONS: tuple[MyRideSensorDescription, ...] = (
    MyRideSensorDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        value_fn=lambda s: s.speed,
    ),
    MyRideSensorDescription(
        key="heading",
        translation_key="heading",
        native_unit_of_measurement=DEGREE,
        value_fn=lambda s: s.heading,
    ),
    MyRideSensorDescription(
        key="bus",
        translation_key="bus",
        value_fn=lambda s: s.bus,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - required by HA's platform setup signature, unused here
    entry: MyRideConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors, three per student, including any discovered later."""

    def make_entities(
        coordinator: MyRideCoordinator, student_id: str
    ) -> list[MyRideSensor]:
        return [
            MyRideSensor(coordinator, student_id, description)
            for description in SENSOR_DESCRIPTIONS
        ]

    async_setup_student_entities(entry.runtime_data, async_add_entities, make_entities)


class MyRideSensor(MyRideStudentEntity, SensorEntity):
    """One data point (speed, heading, or bus) for one student."""

    entity_description: MyRideSensorDescription

    def __init__(
        self,
        coordinator: MyRideCoordinator,
        student_id: str,
        description: MyRideSensorDescription,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, student_id)
        self.entity_description = description
        self._attr_unique_id = f"{student_id}_{description.key}"

    @property
    def native_value(self) -> object:
        """Return the current value, or None if we don't have a snapshot yet."""
        snap = self.snapshot
        return self.entity_description.value_fn(snap) if snap else None
