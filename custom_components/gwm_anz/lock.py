"""Lock platform for GWM ANZ."""
from __future__ import annotations
from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import CONF_SECURITY_PASSWORD
from .entity import GwmAnzEntity, async_call_gwm_api, setup_vehicle_entities, vehicle_value
async def async_setup_entry(hass:HomeAssistant,entry,async_add_entities:AddEntitiesCallback)->None: setup_vehicle_entities(entry,async_add_entities,lambda v:(GwmAnzLock(entry.runtime_data.api,entry.runtime_data.coordinator,v["vin"]),))
class GwmAnzLock(GwmAnzEntity,LockEntity):
    _attr_translation_key="door_lock"
    def __init__(self,api,coordinator,vin): super().__init__(coordinator,vin); self._api=api; self._attr_unique_id=f"{vin}_door_lock"
    @property
    def available(self): return super().available and self.remote_commands_available
    @property
    def is_locked(self): return vehicle_value(self.vehicle,"locked")
    async def async_lock(self, **kwargs): await self._cmd(True)
    async def async_unlock(self, **kwargs): await self._cmd(False)
    async def _cmd(self,lock:bool):
        sec=self.coordinator.config_entry.data.get(CONF_SECURITY_PASSWORD) or self.coordinator.config_entry.options.get(CONF_SECURITY_PASSWORD)
        res=await async_call_gwm_api(self._api.async_lock((self.vehicle or {}).get("encrypted_vin") or self.vin,sec,lock)); await self.coordinator.async_record_command(res.vin,f"queued {res.id}")
