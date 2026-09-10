"""Shared entity base class for MyRide K-12, plus dynamic per-student entity setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import MyRideCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .student_tracker import StudentSnapshot


class MyRideStudentEntity(CoordinatorEntity[MyRideCoordinator]):
    """Base for any entity tied to one student's device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: MyRideCoordinator, student_id: str) -> None:
        """Set up the entity for one student."""
        super().__init__(coordinator)
        self.student_id = student_id
        snapshot = coordinator.data.get(student_id)
        name = snapshot.first_name if snapshot else student_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, student_id)},
            name=name,
            manufacturer="Tyler Technologies",
            model="MyRide K-12 (unofficial)",
        )

    @property
    def snapshot(self) -> StudentSnapshot | None:
        """Return this student's current data, or None if dropped from the roster."""
        return (
            self.coordinator.data.get(self.student_id)
            if self.coordinator.data
            else None
        )

    @property
    def available(self) -> bool:
        """Unavailable once the student no longer appears in the roster."""
        return super().available and self.snapshot is not None


def async_setup_student_entities(
    coordinator: MyRideCoordinator,
    async_add_entities: AddEntitiesCallback,
    make_entities: Callable[[MyRideCoordinator, str], list],
) -> None:
    """
    Add one set of entities per student, including any added in a later roster poll.

    Most integrations know their full entity set at startup. Ours doesn't: a
    student could theoretically be added mid-year, or MyRide could briefly omit
    one from a poll. `make_entities` returns the list of entities for one
    student_id; this wires it up to run for everyone known now and for anyone
    new who shows up later.
    """
    known: set[str] = set()

    def _add_for_new_students() -> None:
        new_ids = set(coordinator.data or {}) - known
        if not new_ids:
            return
        known.update(new_ids)
        entities = [
            entity
            for student_id in new_ids
            for entity in make_entities(coordinator, student_id)
        ]
        async_add_entities(entities)

    _add_for_new_students()
    coordinator.async_add_listener(_add_for_new_students)
