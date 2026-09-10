"""Custom types for the MyRide K-12 integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import MyRideCoordinator

type MyRideConfigEntry = ConfigEntry[MyRideCoordinator]
