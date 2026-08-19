"""Device tracker for GWM ANZ."""
from __future__ import annotations

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GwmAnzEntity, setup_vehicle_entities


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up GWM ANZ device trackers."""
    setup_vehicle_entities(
        entry,
        async_add_entities,
        lambda vehicle: (GwmAnzTracker(entry.runtime_data.coordinator, vehicle["vin"]),),
    )


class GwmAnzTracker(GwmAnzEntity, TrackerEntity):
    """GWM ANZ vehicle tracker."""

    _attr_translation_key = "location"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_location"

    @property
    def latitude(self):
        """Return latitude."""
        return ((self.vehicle or {}).get("location") or {}).get("latitude")

    @property
    def longitude(self):
        """Return longitude."""
        return ((self.vehicle or {}).get("location") or {}).get("longitude")
