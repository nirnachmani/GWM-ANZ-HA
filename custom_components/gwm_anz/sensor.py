"""Sensors for GWM ANZ."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfPressure, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from .entity import GwmAnzEntity, setup_vehicle_entities, vehicle_value
@dataclass(frozen=True, kw_only=True)
class GwmAnzSensorDescription(SensorEntityDescription): value_fn: Callable[[dict[str,Any]|None],Any]
def _v(k): return lambda vehicle: vehicle_value(vehicle,k)
def _ts(k):
    def f(vehicle):
        val=vehicle_value(vehicle,k); return dt_util.parse_datetime(val) if val else None
    return f
SENSORS=(
 GwmAnzSensorDescription(key="soc",translation_key="soc",device_class=SensorDeviceClass.BATTERY,native_unit_of_measurement=PERCENTAGE,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("soc")),
 GwmAnzSensorDescription(key="charging_status",translation_key="charging_status",device_class=SensorDeviceClass.ENUM,options=["disconnected","connected","charging","awaiting_charging","waiting_for_power","error"],value_fn=_v("charging_status")),
 GwmAnzSensorDescription(key="range_km",translation_key="range",device_class=SensorDeviceClass.DISTANCE,native_unit_of_measurement=UnitOfLength.KILOMETERS,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("range_km")),
 GwmAnzSensorDescription(key="fuel_range_km",translation_key="fuel_range",device_class=SensorDeviceClass.DISTANCE,native_unit_of_measurement=UnitOfLength.KILOMETERS,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("fuel_range_km")),
 GwmAnzSensorDescription(key="remaining_charging_time_min",translation_key="remaining_charging_time",device_class=SensorDeviceClass.DURATION,native_unit_of_measurement=UnitOfTime.MINUTES,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("remaining_charging_time_min")),
 GwmAnzSensorDescription(key="charge_mode",translation_key="charge_mode",entity_category=EntityCategory.DIAGNOSTIC,value_fn=_v("charge_mode")),
 GwmAnzSensorDescription(key="tire_pressure_front_left_kpa",translation_key="tire_pressure_front_left",device_class=SensorDeviceClass.PRESSURE,native_unit_of_measurement=UnitOfPressure.KPA,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_pressure_front_left_kpa")),
 GwmAnzSensorDescription(key="tire_pressure_front_right_kpa",translation_key="tire_pressure_front_right",device_class=SensorDeviceClass.PRESSURE,native_unit_of_measurement=UnitOfPressure.KPA,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_pressure_front_right_kpa")),
 GwmAnzSensorDescription(key="tire_pressure_rear_left_kpa",translation_key="tire_pressure_rear_left",device_class=SensorDeviceClass.PRESSURE,native_unit_of_measurement=UnitOfPressure.KPA,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_pressure_rear_left_kpa")),
 GwmAnzSensorDescription(key="tire_pressure_rear_right_kpa",translation_key="tire_pressure_rear_right",device_class=SensorDeviceClass.PRESSURE,native_unit_of_measurement=UnitOfPressure.KPA,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_pressure_rear_right_kpa")),
 GwmAnzSensorDescription(key="tire_temperature_front_left_c",translation_key="tire_temperature_front_left",device_class=SensorDeviceClass.TEMPERATURE,native_unit_of_measurement=UnitOfTemperature.CELSIUS,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_temperature_front_left_c")),
 GwmAnzSensorDescription(key="tire_temperature_front_right_c",translation_key="tire_temperature_front_right",device_class=SensorDeviceClass.TEMPERATURE,native_unit_of_measurement=UnitOfTemperature.CELSIUS,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_temperature_front_right_c")),
 GwmAnzSensorDescription(key="tire_temperature_rear_left_c",translation_key="tire_temperature_rear_left",device_class=SensorDeviceClass.TEMPERATURE,native_unit_of_measurement=UnitOfTemperature.CELSIUS,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_temperature_rear_left_c")),
 GwmAnzSensorDescription(key="tire_temperature_rear_right_c",translation_key="tire_temperature_rear_right",device_class=SensorDeviceClass.TEMPERATURE,native_unit_of_measurement=UnitOfTemperature.CELSIUS,state_class=SensorStateClass.MEASUREMENT,value_fn=_v("tire_temperature_rear_right_c")),
 GwmAnzSensorDescription(key="odometer_km",translation_key="odometer",device_class=SensorDeviceClass.DISTANCE,native_unit_of_measurement=UnitOfLength.KILOMETERS,state_class=SensorStateClass.TOTAL_INCREASING,value_fn=_v("odometer_km")),
 GwmAnzSensorDescription(key="command_status",translation_key="command_status",entity_category=EntityCategory.DIAGNOSTIC,value_fn=lambda v: None if not v else v.get("command_status")),
 GwmAnzSensorDescription(key="update_time",translation_key="update_time",device_class=SensorDeviceClass.TIMESTAMP,entity_category=EntityCategory.DIAGNOSTIC,value_fn=_ts("update_time")),
 GwmAnzSensorDescription(key="acquisition_time",translation_key="acquisition_time",device_class=SensorDeviceClass.TIMESTAMP,entity_category=EntityCategory.DIAGNOSTIC,value_fn=_ts("acquisition_time")),
 GwmAnzSensorDescription(key="last_refresh",translation_key="last_refresh",device_class=SensorDeviceClass.TIMESTAMP,entity_category=EntityCategory.DIAGNOSTIC,value_fn=_ts("last_refresh")),
)
async def async_setup_entry(hass:HomeAssistant,entry,async_add_entities:AddEntitiesCallback)->None:
    setup_vehicle_entities(entry,async_add_entities,lambda vehicle:(GwmAnzSensor(entry.runtime_data.coordinator,vehicle["vin"],d) for d in SENSORS))
class GwmAnzSensor(GwmAnzEntity,SensorEntity):
    entity_description:GwmAnzSensorDescription
    def __init__(self,coordinator,vin,description): super().__init__(coordinator,vin); self.entity_description=description; self._attr_unique_id=f"{vin}_{description.key}"
    @property
    def native_value(self): return self.entity_description.value_fn(self.vehicle)
    @property
    def extra_state_attributes(self):
        if self.entity_description.key != "soc":
            return None
        vehicle = self.vehicle or {}
        return {"encrypted_vin": vehicle.get("encrypted_vin"), "raw_items": vehicle.get("raw_items")}
