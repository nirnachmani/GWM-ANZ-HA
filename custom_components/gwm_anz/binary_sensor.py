"""Binary sensors for GWM ANZ."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .entity import GwmAnzEntity, setup_vehicle_entities, vehicle_value
@dataclass(frozen=True, kw_only=True)
class Desc(BinarySensorEntityDescription): value_fn:Callable[[dict[str,Any]|None],Any]
def _v(k): return lambda veh: vehicle_value(veh,k)
DESCS=(Desc(key="charging_active",translation_key="charging_active",device_class=BinarySensorDeviceClass.BATTERY_CHARGING,value_fn=_v("charging_active")),Desc(key="charge_plug_connected",translation_key="charge_plug_connected",device_class=BinarySensorDeviceClass.PLUG,value_fn=_v("charge_plug_connected")),Desc(key="lock_open",translation_key="lock_open",device_class=BinarySensorDeviceClass.LOCK,value_fn=lambda v: None if vehicle_value(v,"locked") is None else not vehicle_value(v,"locked")),Desc(key="ac_active",translation_key="ac_active",value_fn=_v("ac_active")),Desc(key="window_front_left_open",translation_key="window_front_left_open",device_class=BinarySensorDeviceClass.WINDOW,value_fn=_v("window_front_left_open")),Desc(key="window_front_right_open",translation_key="window_front_right_open",device_class=BinarySensorDeviceClass.WINDOW,value_fn=_v("window_front_right_open")),Desc(key="window_rear_left_open",translation_key="window_rear_left_open",device_class=BinarySensorDeviceClass.WINDOW,value_fn=_v("window_rear_left_open")),Desc(key="window_rear_right_open",translation_key="window_rear_right_open",device_class=BinarySensorDeviceClass.WINDOW,value_fn=_v("window_rear_right_open")))
async def async_setup_entry(hass:HomeAssistant,entry,async_add_entities:AddEntitiesCallback)->None: setup_vehicle_entities(entry,async_add_entities,lambda v:(GwmAnzBinarySensor(entry.runtime_data.coordinator,v["vin"],d) for d in DESCS))
class GwmAnzBinarySensor(GwmAnzEntity,BinarySensorEntity):
    entity_description:Desc
    def __init__(self,coordinator,vin,description): super().__init__(coordinator,vin); self.entity_description=description; self._attr_unique_id=f"{vin}_{description.key}"
    @property
    def is_on(self): return self.entity_description.value_fn(self.vehicle)
