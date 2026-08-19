"""Number platform for GWM ANZ."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import GwmAnzEntity, async_call_gwm_api, setup_vehicle_entities, vehicle_value


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up GWM ANZ number entities."""
    setup_vehicle_entities(
        entry,
        async_add_entities,
        lambda vehicle: (
            GwmAnzAcTemperature(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzOperationMinutes(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzDefrostMinutes(entry.runtime_data.coordinator, vehicle["vin"], "front"),
            GwmAnzDefrostMinutes(entry.runtime_data.coordinator, vehicle["vin"], "rear"),
            GwmAnzSeatLevel(entry.runtime_data.coordinator, vehicle["vin"], "front_left"),
            GwmAnzSeatLevel(entry.runtime_data.coordinator, vehicle["vin"], "front_right"),
            GwmAnzSeatLevel(entry.runtime_data.coordinator, vehicle["vin"], "rear_left"),
            GwmAnzSeatLevel(entry.runtime_data.coordinator, vehicle["vin"], "rear_right"),
            GwmAnzSeatMinutes(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzSteeringWheelMinutes(entry.runtime_data.coordinator, vehicle["vin"]),
            GwmAnzStartVehicleMinutes(entry.runtime_data.coordinator, vehicle["vin"]),
        ),
    )


class GwmAnzAcTemperature(GwmAnzEntity, NumberEntity):
    """Staged remote A/C temperature (the app's temperature selector).

    Reads the persisted cloud value ``vehicleBasicsInfo.config.airConditionerTemperature``
    and writes it back via ``vehicle/modifyVehicleRemoteCtlInfo``. This mirrors the
    app, which keeps the temperature selector separate from the A/C on/off toggle.
    """

    _attr_translation_key = "ac_temperature"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 16
    _attr_native_max_value = 32
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_ac_temperature"

    @property
    def available(self) -> bool:
        """Return whether A/C temperature commands are available."""
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        """Return the persisted A/C target temperature in °C."""
        return vehicle_value(self.vehicle, "ac_temperature_c")

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new A/C temperature via modifyVehicleRemoteCtlInfo."""
        temperature = int(round(value))
        minutes = vehicle_value(self.vehicle, "operation_time_minutes") or 15
        await async_call_gwm_api(
            self.coordinator.api.async_modify_vehicle_remote_ctl_info(
                (self.vehicle or {}).get("encrypted_vin") or self.vin,
                temperature=temperature,
                minutes=minutes,
            )
        )
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("climate", {})["ac_temperature_c"] = float(temperature)
        self.async_write_ha_state()


class GwmAnzOperationMinutes(GwmAnzEntity, NumberEntity):
    """Local climate run-time helper for the next T5 A/C command."""

    _attr_translation_key = "operation_time"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 5
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_operation_time"
        self._local_value: int | None = None

    @property
    def available(self) -> bool:
        """Return whether climate commands are available."""
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        """Return operation time in minutes."""
        return self._local_value if self._local_value is not None else vehicle_value(self.vehicle, "operation_time_minutes")

    async def async_set_native_value(self, value: float) -> None:
        """Set local operation time used by this HA integration's next A/C command."""
        if not float(value).is_integer() or value < 5 or value > 30:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_operation_time",
            )
        self._local_value = int(value)
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("climate", {})["operation_time_minutes"] = self._local_value
        self.async_write_ha_state()


class GwmAnzDefrostMinutes(GwmAnzEntity, NumberEntity):
    """Front/rear demisting duration (minutes), persisted to the cloud config."""

    _POSITIONS = {
        "front": ("front_defrost_minutes", "front_defrost_time_minutes", "frontDefrostTime"),
        "rear": ("rear_defrost_minutes", "rear_defrost_time_minutes", "rearDefrostTime"),
    }

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, vin: str, position: str) -> None:
        super().__init__(coordinator, vin)
        self._position = position
        self._attr_translation_key = self._POSITIONS[position][0]
        self._attr_unique_id = f"{vin}_{position}_defrost_minutes"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        return vehicle_value(self.vehicle, self._POSITIONS[self._position][1])

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(round(value))
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        persist_key = self._POSITIONS[self._position][2]
        await async_call_gwm_api(
            self.coordinator.api.async_persist_remote_ctl_info(vin, {persist_key: str(minutes * 60)})
        )
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("remote_settings", {})[self._POSITIONS[self._position][1]] = float(minutes)
        self.async_write_ha_state()


class GwmAnzSeatLevel(GwmAnzEntity, NumberEntity):
    """Per-seat heating/ventilation level (0-9), persisted to the cloud config."""

    _POSITIONS = {
        "front_left": ("seat_front_left", "seat_front_left", "leftFrontSeat"),
        "front_right": ("seat_front_right", "seat_front_right", "rightFrontSeat"),
        "rear_left": ("seat_rear_left", "seat_rear_left", "leftBackSeat"),
        "rear_right": ("seat_rear_right", "seat_rear_right", "rightBackSeat"),
    }

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 9
    _attr_native_step = 1

    def __init__(self, coordinator, vin: str, position: str) -> None:
        super().__init__(coordinator, vin)
        self._position = position
        self._attr_translation_key = self._POSITIONS[position][0]
        self._attr_unique_id = f"{vin}_{position}"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        return vehicle_value(self.vehicle, self._POSITIONS[self._position][1])

    async def async_set_native_value(self, value: float) -> None:
        level = int(round(value))
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        persist_key = self._POSITIONS[self._position][2]
        await async_call_gwm_api(
            self.coordinator.api.async_persist_remote_ctl_info(vin, {persist_key: str(level)})
        )
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("remote_settings", {})[self._POSITIONS[self._position][1]] = float(level)
        self.async_write_ha_state()


class GwmAnzSeatMinutes(GwmAnzEntity, NumberEntity):
    """Seat heating/ventilation run time (minutes), persisted to the cloud config."""

    _attr_translation_key = "seat_minutes"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_seat_minutes"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        return vehicle_value(self.vehicle, "seat_control_time_minutes")

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(round(value))
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        await async_call_gwm_api(
            self.coordinator.api.async_persist_remote_ctl_info(vin, {"seatHeatingControlTime": str(minutes * 60)})
        )
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("remote_settings", {})["seat_control_time_minutes"] = float(minutes)
        self.async_write_ha_state()


class GwmAnzSteeringWheelMinutes(GwmAnzEntity, NumberEntity):
    """Heated steering wheel run time (minutes), persisted to the cloud config."""

    _attr_translation_key = "steering_wheel_minutes"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_steering_wheel_minutes"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        return vehicle_value(self.vehicle, "steering_wheel_time_minutes")

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(round(value))
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        await async_call_gwm_api(
            self.coordinator.api.async_persist_remote_ctl_info(vin, {"steeringWheelHeatingTime": str(minutes * 60)})
        )
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("remote_settings", {})["steering_wheel_time_minutes"] = float(minutes)
        self.async_write_ha_state()


class GwmAnzStartVehicleMinutes(GwmAnzEntity, NumberEntity):
    """Remote engine start run time (minutes), persisted to the cloud config."""

    _attr_translation_key = "start_vehicle_minutes"
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, vin: str) -> None:
        super().__init__(coordinator, vin)
        self._attr_unique_id = f"{vin}_start_vehicle_minutes"

    @property
    def available(self) -> bool:
        return super().available and self.remote_commands_available

    @property
    def native_value(self):
        return vehicle_value(self.vehicle, "engine_time_minutes")

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(round(value))
        vin = (self.vehicle or {}).get("encrypted_vin") or self.vin
        await async_call_gwm_api(
            self.coordinator.api.async_persist_remote_ctl_info(vin, {"engineStatusTime": str(minutes * 60)})
        )
        vehicle = self.vehicle
        if vehicle is not None:
            vehicle.setdefault("remote_settings", {})["engine_time_minutes"] = float(minutes)
        self.async_write_ha_state()

