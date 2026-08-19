"""Coordinator for GWM ANZ."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GwmAnzApiError, GwmAnzAuthError, GwmAnzClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_PENDING_RESULT_CODE = "2000"
_SUCCESS_RESULT_CODES = {"0", "6"}
_POST_COMMAND_REFRESH_DELAYS = (20, 35)
_OPTIMISTIC_CLIMATE_SECONDS = 25
# Consecutive auth failures before we give up and require re-auth. Transient
# token-refresh failures are retryable (UpdateFailed keeps polling); only a
# sustained run of failures should stop the coordinator (ConfigEntryAuthFailed).
_MAX_CONSECUTIVE_AUTH_FAILURES = 20


class GwmAnzCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for GWM ANZ polling and command state."""

    def __init__(self, hass: HomeAssistant, api: GwmAnzClient, poll_interval: int) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=poll_interval))
        self.api = api
        self.command_status: dict[str, str] = {}
        self._pending_commands: dict[str, str] = {}
        self._optimistic_climate: dict[str, dict[str, Any]] = {}
        self._optimistic_values: dict[str, dict[str, Any]] = {}
        self._optimistic_command_keys: dict[str, set[str]] = {}
        self._auth_failures = 0

    async def _async_update_data(self) -> dict[str, Any]:
        _LOGGER.debug("Poll start")
        try:
            data = await self.api.async_refresh()
            self._auth_failures = 0
            entry = getattr(self, "config_entry", None)
            if entry is not None:
                tokens = self.api.tokens
                if any(entry.data.get(key) != value for key, value in tokens.items()):
                    self.hass.config_entries.async_update_entry(entry, data={**entry.data, **tokens})
            await self._poll_pending_commands()
            self._apply_optimistic_climate(data)
            self._apply_optimistic_values(data)
            for vehicle in data.get("vehicles", []):
                encrypted_vin = vehicle.get("encrypted_vin")
                if encrypted_vin in self.command_status:
                    vehicle["command_status"] = self.command_status[encrypted_vin]
                ts = vehicle.get("timestamps") or {}
                _LOGGER.debug(
                    "Poll ok: vin=%s last_refresh=%s acquisition=%s update=%s",
                    encrypted_vin, ts.get("last_refresh"), ts.get("acquisition_time"), ts.get("update_time"),
                )
            return data
        except GwmAnzAuthError as err:
            self._auth_failures += 1
            _LOGGER.debug("Poll auth error (fail %s/%s): %s", self._auth_failures, _MAX_CONSECUTIVE_AUTH_FAILURES, err)
            if self._auth_failures >= _MAX_CONSECUTIVE_AUTH_FAILURES:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err
        except GwmAnzApiError as err:
            _LOGGER.debug("Poll api error: %s", err)
            raise UpdateFailed(str(err)) from err

    @property
    def vehicles(self) -> list[dict[str, Any]]:
        return list((self.data or {}).get("vehicles", []))

    def vehicle(self, vin: str) -> dict[str, Any] | None:
        return next((v for v in self.vehicles if v.get("vin") == vin or v.get("encrypted_vin") == vin), None)

    async def async_record_command(self, encrypted_vin: str, status: str) -> None:
        """Record a command status and trigger confirmation refreshes."""
        self.command_status[encrypted_vin] = status
        if status.startswith("queued "):
            self._pending_commands[encrypted_vin] = status.removeprefix("queued ").strip()
        self._set_vehicle_value(encrypted_vin, "command_status", status)
        self._schedule_confirmation_refreshes()

    async def async_record_climate_command(self, encrypted_vin: str, status: str, *, mode: str, temperature: int | None) -> None:
        """Optimistically reflect a climate command while cloud status catches up."""
        self.command_status[encrypted_vin] = status
        if status.startswith("queued "):
            self._pending_commands[encrypted_vin] = status.removeprefix("queued ").strip()
        data = deepcopy(self.data or {})
        for vehicle in data.get("vehicles", []):
            if encrypted_vin not in {vehicle.get("vin"), vehicle.get("encrypted_vin")}:
                continue
            active = mode != "off"
            vehicle["command_status"] = status
            vehicle.setdefault("values", {})["ac_active"] = active
            climate = vehicle.setdefault("climate", {})
            climate["mode"] = "on" if active else "off"
            if active and temperature is not None:
                climate["ac_temperature_c"] = float(temperature)
            # When turning off, keep the persisted A/C temperature so the
            # ac_temperature number entity still shows the staged value.
            self._optimistic_climate[encrypted_vin] = {
                "expires_at": time.monotonic() + _OPTIMISTIC_CLIMATE_SECONDS,
                "mode": mode,
                "temperature": temperature,
                "status": status,
            }
            break
        self.async_set_updated_data(data)
        self._schedule_confirmation_refreshes()

    def _apply_optimistic_climate(self, data: dict[str, Any]) -> None:
        """Keep climate optimistic until the vehicle/cloud has had time to catch up."""
        now = time.monotonic()
        for encrypted_vin, optimistic in list(self._optimistic_climate.items()):
            if optimistic["expires_at"] <= now:
                self._optimistic_climate.pop(encrypted_vin, None)
                continue
            for vehicle in data.get("vehicles", []):
                if encrypted_vin not in {vehicle.get("vin"), vehicle.get("encrypted_vin")}:
                    continue
                active = optimistic["mode"] != "off"
                vehicle.setdefault("values", {})["ac_active"] = active
                climate = vehicle.setdefault("climate", {})
                climate["mode"] = "on" if active else "off"
                if active and optimistic.get("temperature") is not None:
                    climate["ac_temperature_c"] = float(optimistic["temperature"])
                vehicle["command_status"] = optimistic["status"]
                break

    def _set_vehicle_value(self, encrypted_vin: str, key: str, value: Any) -> None:
        """Update one vehicle field locally without waiting for the next poll."""
        if not self.data:
            return
        data = deepcopy(self.data)
        changed = False
        for vehicle in data.get("vehicles", []):
            if encrypted_vin in {vehicle.get("vin"), vehicle.get("encrypted_vin")}:
                vehicle[key] = value
                changed = True
        if changed:
            self.async_set_updated_data(data)

    def set_optimistic_value(self, encrypted_vin: str, key: str, value: Any, *, clear_on_complete: bool = True) -> None:
        """Record an optimistic value that is re-applied after each poll.

        ``clear_on_complete=True`` (default) ties the value to the pending remote
        command: it is held while the command is queued/in-progress and cleared once
        the command reports completed/failed, so the synced cloud state (e.g. engine
        status, defrost state) then takes over. ``clear_on_complete=False`` keeps the
        value indefinitely, for switches whose cloud state never round-trips (seat,
        heated steering wheel).
        """
        self._optimistic_values.setdefault(encrypted_vin, {})[key] = value
        if clear_on_complete:
            self._optimistic_command_keys.setdefault(encrypted_vin, set()).add(key)
        data = deepcopy(self.data or {})
        for vehicle in data.get("vehicles", []):
            if encrypted_vin in {vehicle.get("vin"), vehicle.get("encrypted_vin")}:
                vehicle.setdefault("values", {})[key] = value
                break
        self.async_set_updated_data(data)

    def _clear_command_optimistic(self, encrypted_vin: str) -> None:
        """Drop optimistic values that were tied to the now-finished command."""
        keys = self._optimistic_command_keys.pop(encrypted_vin, None)
        if not keys:
            return
        optimistic = self._optimistic_values.get(encrypted_vin, {})
        for key in keys:
            optimistic.pop(key, None)
        if not optimistic:
            self._optimistic_values.pop(encrypted_vin, None)
        _LOGGER.debug("Cleared command-tied optimistic values for %s: %s", encrypted_vin, sorted(keys))

    def _apply_optimistic_values(self, data: dict[str, Any]) -> None:
        """Re-apply optimistic switch values over freshly polled data."""
        for encrypted_vin, values in self._optimistic_values.items():
            for vehicle in data.get("vehicles", []):
                if encrypted_vin not in {vehicle.get("vin"), vehicle.get("encrypted_vin")}:
                    continue
                for key, value in values.items():
                    vehicle.setdefault("values", {})[key] = value
                break

    def _schedule_confirmation_refreshes(self) -> None:
        """Refresh shortly after remote commands so optimistic state is verified."""
        for delay in _POST_COMMAND_REFRESH_DELAYS:
            self.hass.async_create_task(self._delayed_refresh(delay))

    async def _delayed_refresh(self, delay: int) -> None:
        await asyncio.sleep(delay)
        await self.async_request_refresh()

    async def _poll_pending_commands(self) -> None:
        for encrypted_vin, seq_no in list(self._pending_commands.items()):
            try:
                result = await self.api.async_command_status(seq_no, encrypted_vin)
            except GwmAnzApiError as err:
                _LOGGER.debug("Remote command result poll failed for %s: %s", encrypted_vin, err)
                continue
            item = self._select_command_result(result, seq_no)
            if not item:
                self.command_status[encrypted_vin] = f"waiting for result {seq_no}"
                continue
            code = str(item.get("resultCode") or "")
            msg = str(item.get("resultMsg") or "")
            if code == _PENDING_RESULT_CODE:
                self.command_status[encrypted_vin] = f"in progress {seq_no}: {msg or code}"
                continue
            success = code in _SUCCESS_RESULT_CODES or msg.lower() == "success"
            self.command_status[encrypted_vin] = f"{'completed' if success else 'failed'} {seq_no}: {msg or code or 'unknown result'}"
            _LOGGER.debug("Remote command %s -> %s (success=%s)", seq_no, code, success)
            if not success:
                self._optimistic_climate.pop(encrypted_vin, None)
            self._pending_commands.pop(encrypted_vin, None)
            self._clear_command_optimistic(encrypted_vin)

    @staticmethod
    def _select_command_result(result: Any, seq_no: str) -> dict[str, Any] | None:
        if isinstance(result, dict):
            candidates = result.get("data") or result.get("items") or result.get("list") or [result]
        else:
            candidates = result
        if not isinstance(candidates, list):
            return None
        dicts = [item for item in candidates if isinstance(item, dict)]
        return next((item for item in dicts if str(item.get("hwCommandId") or "").lower() == seq_no.lower()), None) or (dicts[0] if dicts else None)
