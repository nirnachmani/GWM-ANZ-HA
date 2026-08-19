"""Select platform for GWM ANZ — seat heating/ventilation mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GwmAnzEntity, async_call_gwm_api, setup_vehicle_entities, vehicle_value

_OPTION_TO_MODE = {"heat": "1", "ventilation": "2"}
_MODE_TO_OPTION = {"1": "heat", "2": "ventilation"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_vehicle_entities(
        entry,
        async_add_entities,
        lambda vehicle: (GwmAnzSeatModeSelect(entry.runtime_data.coordinator, vehicle["vin"]),),
    )


class GwmAnzSeatModeSelect(GwmAnzEntity, SelectEntity):
    """Seat climate mode (heat vs ventilation). Persisted to the cloud config."""

    _attr_translation_key = "seat_mode"
    _attr_options = ["heat", "ventilation"]

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_seat_mode"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def current_option(self) -> str | None:
        return _MODE_TO_OPTION.get(str(vehicle_value(self.vehicle, "seat_heating_type")))

    async def async_select_option(self, option: str) -> None:
        mode = _OPTION_TO_MODE.get(option)
        if mode is None:
            return
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        await async_call_gwm_api(
            self.coordinator.api.async_persist_remote_ctl_info(vin, {"seatHeatingType": mode})
        )
