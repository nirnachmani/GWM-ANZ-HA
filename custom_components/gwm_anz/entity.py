"""Base entities for GWM ANZ."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from .api import GwmAnzApiError, GwmAnzAuthError
from .const import DOMAIN


def vehicle_value(vehicle: dict[str, Any] | None, key: str) -> Any:
    """Return a mapped value from a vehicle snapshot."""
    if not vehicle:
        return None
    if key in vehicle:
        return vehicle.get(key)
    for section in ("values", "timestamps", "climate", "capabilities", "remote_settings"):
        if isinstance(vehicle.get(section), dict) and key in vehicle[section]:
            return vehicle[section].get(key)
    return None


class GwmAnzEntity(CoordinatorEntity):
    """Base entity bound to one GWM ANZ vehicle."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator)
        self.vin = vin

    @property
    def vehicle(self) -> dict[str, Any] | None:
        """Return the current vehicle snapshot."""
        return self.coordinator.vehicle(self.vin)

    @property
    def available(self) -> bool:
        """Return whether the entity has coordinator data for its vehicle."""
        return super().available and self.vehicle is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return stable device registry info."""
        vehicle = self.vehicle or {}
        ident = vehicle.get("encrypted_vin") or self.vin
        return DeviceInfo(
            identifiers={(DOMAIN, ident)},
            name=vehicle.get("name") or "GWM ANZ",
            manufacturer=vehicle.get("manufacturer") or "GWM",
            model=vehicle.get("model"),
            serial_number=vehicle.get("plain_vin") or vehicle.get("serial_number"),
        )

    @property
    def remote_commands_available(self) -> bool:
        """Return whether remote commands appear available for this vehicle."""
        vehicle = self.vehicle or {}
        capabilities = vehicle.get("capabilities") or {}
        if "remote_commands" in capabilities:
            return bool(capabilities.get("remote_commands"))
        return True


def setup_vehicle_entities(
    entry,
    async_add_entities,
    factory: Callable[[dict[str, Any]], Iterable[GwmAnzEntity]],
) -> None:
    """Add entities for all current and newly discovered vehicles."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    def add() -> None:
        entities: list[GwmAnzEntity] = []
        for vehicle in coordinator.vehicles:
            vin = vehicle.get("vin")
            if not vin or vin in known:
                continue
            known.add(vin)
            entities.extend(factory(vehicle))
        if entities:
            async_add_entities(entities)

    add()
    entry.async_on_unload(coordinator.async_add_listener(add))


async def async_call_gwm_api(call):
    """Call the GWM API and raise translated Home Assistant errors."""
    try:
        return await call
    except GwmAnzAuthError as err:
        raise HomeAssistantError(f"GWM ANZ authentication failed: {err}") from err
    except GwmAnzApiError as err:
        raise HomeAssistantError(f"GWM ANZ request failed: {err}") from err
