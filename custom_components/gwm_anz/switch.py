"""Switch platform for GWM ANZ — remote climate/seat controls."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SECURITY_PASSWORD
from .entity import GwmAnzEntity, async_call_gwm_api, setup_vehicle_entities, vehicle_value


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    setup_vehicle_entities(
        entry,
        async_add_entities,
        lambda vehicle: (
            GwmAnzAirCirculationSwitch(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzChargingSwitch(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzDefrostSwitch(entry.runtime_data.coordinator, vehicle["vin"], "front"),
            GwmAnzDefrostSwitch(entry.runtime_data.coordinator, vehicle["vin"], "rear"),
            GwmAnzSeatSwitch(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzSteeringWheelSwitch(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzStartVehicleSwitch(entry.runtime_data.coordinator, vehicle["vin"]),
        ),
    )


def _security_password(coordinator) -> str:
    return (
        coordinator.config_entry.data.get(CONF_SECURITY_PASSWORD)
        or coordinator.config_entry.options.get(CONF_SECURITY_PASSWORD)
    )


class GwmAnzAirCirculationSwitch(GwmAnzEntity, SwitchEntity):
    """Air circulation toggle (fixed ~60s burst, remote type 0x11)."""

    _attr_translation_key = "air_circulation"

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_air_circulation"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def is_on(self) -> bool:
        return vehicle_value(self.vehicle, "circulation_active") is True

    async def async_turn_on(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_circulation(vin, _security_password(self.coordinator), switch_order=1)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")

    async def async_turn_off(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_circulation(vin, _security_password(self.coordinator), switch_order=2)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")


class GwmAnzChargingSwitch(GwmAnzEntity, SwitchEntity):
    """Charge-now / stop-charging toggle (remote type 0x01)."""

    _attr_translation_key = "charging"

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_charging"

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.remote_commands_available
            and vehicle_value(self.vehicle, "charge_plug_connected") is True
        )

    @property
    def is_on(self) -> bool:
        return vehicle_value(self.vehicle, "charging_active") is True

    async def async_turn_on(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_charging(vin, _security_password(self.coordinator), switch_order=1)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "charging_active", True, clear_on_complete=True)

    async def async_turn_off(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_charging(vin, _security_password(self.coordinator), switch_order=2)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "charging_active", False, clear_on_complete=True)


class GwmAnzDefrostSwitch(GwmAnzEntity, SwitchEntity):
    """Front/rear demisting toggle (remote type 0x0B)."""

    _FRONT = "front"
    _REAR = "rear"

    def __init__(self, coordinator, vin: str, position: str) -> None:
        super().__init__(coordinator, vin)
        self._position = position  # "front" or "rear"
        self._attr_translation_key = f"{position}_defrost"
        self._attr_unique_id = f"{vin}_{position}_defrost"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def _active_key(self) -> str:
        return "front_defrost_active" if self._position == self._FRONT else "rear_defrost_active"

    @property
    def _time_key(self) -> str:
        return "front_defrost_time_minutes" if self._position == self._FRONT else "rear_defrost_time_minutes"

    @property
    def is_on(self) -> bool:
        return vehicle_value(self.vehicle, self._active_key) is True

    async def async_turn_on(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        minutes = vehicle_value(self.vehicle, self._time_key) or 15
        body = {"defrost_front": 1} if self._position == self._FRONT else {"defrost_back": 1}
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_defrost(
                vin, _security_password(self.coordinator), operation_time=int(minutes), switch_order=1, **body
            )
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, self._active_key, True, clear_on_complete=True)

    async def async_turn_off(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        body = {"defrost_front": 1} if self._position == self._FRONT else {"defrost_back": 1}
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_defrost(
                vin, _security_password(self.coordinator), switch_order=2, **body
            )
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, self._active_key, False, clear_on_complete=True)


class GwmAnzSeatSwitch(GwmAnzEntity, SwitchEntity):
    """Seat heating/ventilation master on/off (remote type 0x0A).

    Reads live state from getLastStatus seat codes (2220001/2220002 heat,
    2220003/2220004 vent, 2424001/2424002 rear heat, 2220018/2220021 rear vent),
    which carry the level (1-9) when on and "0" when off. The staged config
    (leftFrontSeat etc.) is only the remembered preset level, so it must NOT be
    used as the on/off source. Optimistic value is held while the command is
    queued/in-progress and cleared on completion so the live state takes over.
    """

    _attr_translation_key = "seat"

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_seat"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def is_on(self) -> bool:
        return vehicle_value(self.vehicle, "seat_active") is True

    async def async_turn_on(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        mode = vehicle_value(self.vehicle, "seat_heating_type") or "1"
        minutes = vehicle_value(self.vehicle, "seat_control_time_minutes") or 5
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_seat(
                vin,
                _security_password(self.coordinator),
                operation_mode=mode,
                operation_time=int(minutes),
                left_front=vehicle_value(self.vehicle, "seat_front_left") or 0,
                right_front=vehicle_value(self.vehicle, "seat_front_right") or 0,
                left_back=vehicle_value(self.vehicle, "seat_rear_left") or 0,
                right_back=vehicle_value(self.vehicle, "seat_rear_right") or 0,
                switch_order=1,
            )
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "seat_active", True, clear_on_complete=True)

    async def async_turn_off(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_seat(vin, _security_password(self.coordinator), switch_order=2)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "seat_active", False, clear_on_complete=True)


class GwmAnzSteeringWheelSwitch(GwmAnzEntity, SwitchEntity):
    """Heated steering wheel (remote type 0x19).

    Reads live state from getLastStatus code 2060016 (steerWheelHeatSts), which
    carries "0" when off and a non-zero value when on — the same code appears in
    the MQTT dde-status push. The staged config steeringWheelHeatingTime is only
    the remembered duration preset, not the on/off state.
    """

    _attr_translation_key = "steering_wheel"

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_steering_wheel"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def is_on(self) -> bool:
        return vehicle_value(self.vehicle, "steering_wheel_active") is True

    async def async_turn_on(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        minutes = vehicle_value(self.vehicle, "steering_wheel_time_minutes") or 15
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_steering_wheel(vin, _security_password(self.coordinator), switch_order=1, minutes=int(minutes))
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "steering_wheel_active", True, clear_on_complete=True)

    async def async_turn_off(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_set_steering_wheel(vin, _security_password(self.coordinator), switch_order=2)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "steering_wheel_active", False, clear_on_complete=True)


class GwmAnzStartVehicleSwitch(GwmAnzEntity, SwitchEntity):
    """Remote engine start/stop (remote type 0x03). State from engine status."""

    _attr_translation_key = "start_vehicle"

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_start_vehicle"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def is_on(self) -> bool:
        return vehicle_value(self.vehicle, "engine_running") is True

    async def async_turn_on(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        minutes = vehicle_value(self.vehicle, "engine_time_minutes") or 10
        res = await async_call_gwm_api(
            self.coordinator.api.async_start_vehicle(vin, _security_password(self.coordinator), switch_order=1, minutes=int(minutes))
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "engine_running", True)

    async def async_turn_off(self, **kwargs) -> None:
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        res = await async_call_gwm_api(
            self.coordinator.api.async_start_vehicle(vin, _security_password(self.coordinator), switch_order=2)
        )
        await self.coordinator.async_record_command(res.vin, f"queued {res.id}")
        self.coordinator.set_optimistic_value(vin, "engine_running", False)
