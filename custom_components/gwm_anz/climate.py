"""Climate platform for GWM ANZ."""
from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import HVACAction
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SECURITY_PASSWORD, DOMAIN
from .entity import GwmAnzEntity, async_call_gwm_api, setup_vehicle_entities, vehicle_value


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up GWM ANZ climate entities."""
    setup_vehicle_entities(
        entry,
        async_add_entities,
        lambda vehicle: (GwmAnzClimate(entry.runtime_data.api, entry.runtime_data.coordinator, vehicle["vin"]),),
    )


class GwmAnzClimate(GwmAnzEntity, ClimateEntity):
    """GWM ANZ remote A/C on/off switch.

    The app separates the A/C on/off toggle from the temperature selector. This
    entity is the on/off toggle only; the temperature selector is exposed as a
    dedicated number entity (``ac_temperature``) that reads/writes the persisted
    cloud value ``airConditionerTemperature``.
    """

    _attr_translation_key = "climate"
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, api, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._api = api
        self._attr_unique_id = f"{vin}_climate"

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """No temperature control on this entity — that is the ac_temperature number entity."""
        return ClimateEntityFeature(0)

    @property
    def available(self):
        """Return whether the climate entity is available."""
        return super().available and self.remote_commands_available

    @property
    def current_temperature(self):
        """Return cabin temperature (not provided by the Tank 500's telemetry)."""
        return None

    @property
    def hvac_mode(self):
        """Return on/off state as AUTO because the app has no heat/cool choice."""
        return HVACMode.AUTO if vehicle_value(self.vehicle, "ac_active") else HVACMode.OFF

    @property
    def hvac_action(self):
        """Return active/off action without claiming cooling vs heating."""
        return HVACAction.IDLE if vehicle_value(self.vehicle, "ac_active") else HVACAction.OFF

    async def async_set_hvac_mode(self, hvac_mode):
        """Turn remote A/C on or off."""
        if hvac_mode not in (HVACMode.OFF, HVACMode.AUTO):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unsupported_climate_mode",
            )
        # The temperature is the persisted value from the ac_temperature number
        # entity; the app applies it when A/C is next turned on.
        temperature = int(vehicle_value(self.vehicle, "ac_temperature_c") or 22)
        await self._send("off" if hvac_mode == HVACMode.OFF else "on", temperature)

    async def async_turn_on(self) -> None:
        """Turn remote A/C on."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        """Turn remote A/C off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _send(self, mode: str, temp: int) -> None:
        sec = self.coordinator.config_entry.options.get(CONF_SECURITY_PASSWORD) or self.coordinator.config_entry.data.get(CONF_SECURITY_PASSWORD)
        result = await async_call_gwm_api(
            self._api.async_set_climate(
                (self.vehicle or {}).get("encrypted_vin") or self.vin,
                sec,
                mode=mode,
                temperature=temp,
                minutes=vehicle_value(self.vehicle, "operation_time_minutes") or 15,
            )
        )
        await self.coordinator.async_record_climate_command(
            result.vin,
            f"queued {result.id}",
            mode=mode,
            temperature=temp,
        )
