"""Buttons for GWM ANZ."""
from __future__ import annotations
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import CONF_SECURITY_PASSWORD
from .entity import GwmAnzEntity, async_call_gwm_api, setup_vehicle_entities


def _security_password(coordinator) -> str:
    return (
        coordinator.config_entry.data.get(CONF_SECURITY_PASSWORD)
        or coordinator.config_entry.options.get(CONF_SECURITY_PASSWORD)
    )


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_vehicle_entities(
        entry,
        async_add_entities,
        lambda v: (
            GwmAnzCloseWindowsButton(entry.runtime_data.api, entry.runtime_data.coordinator, v["vin"]),
            GwmAnzCloseSunroofButton(entry.runtime_data.api, entry.runtime_data.coordinator, v["vin"]),
        ),
    )


class GwmAnzCloseWindowsButton(GwmAnzEntity, ButtonEntity):
    _attr_translation_key = "close_windows"

    def __init__(self, api, coordinator, vin):
        super().__init__(coordinator, vin)
        self._api = api
        self._attr_unique_id = f"{vin}_close_windows"

    @property
    def available(self):
        return super().available and self.remote_commands_available

    async def async_press(self):
        sec = _security_password(self.coordinator)
        res = await async_call_gwm_api(self._api.async_close_windows((self.vehicle or {}).get("encrypted_vin") or self.vin, sec))
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")


class GwmAnzCloseSunroofButton(GwmAnzEntity, ButtonEntity):
    _attr_translation_key = "close_sunroof"

    def __init__(self, api, coordinator, vin):
        super().__init__(coordinator, vin)
        self._api = api
        self._attr_unique_id = f"{vin}_close_sunroof"

    @property
    def available(self):
        return super().available and self.remote_commands_available

    async def async_press(self):
        sec = _security_password(self.coordinator)
        res = await async_call_gwm_api(self._api.async_close_sunroof((self.vehicle or {}).get("encrypted_vin") or self.vin, sec))
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
