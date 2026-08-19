"""GWM ANZ Cloud integration."""
from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GwmAnzApiError, GwmAnzAuthError, GwmAnzClient
from .const import *
from .coordinator import GwmAnzCoordinator


GWM_ANZ_SERVICES = ['refresh', 'send_raw_command', 'set_windows', 'search_vehicle', 'set_defrost', 'set_circulation', 'set_seat', 'set_light', 'set_activation', 'set_idle_charging', 'send_t5_charging_instruction', 'get_vehicle_charging_info', 'get_vehicle_charge_logs', 'set_vehicle_charging_plan', 'query_battery_preheat_plan', 'set_battery_preheat_plan', 'get_compound_templates', 'get_compound_template_info', 'check_security_password', 'open_sliding_door', 'cancel_sliding_door', 'charge_station_status', 'charge_station_info', 'charge_station_points', 'charge_station_new_points', 'charge_station_create_session', 'charge_station_check_in', 'charge_station_check_out', 'charge_station_unlock', 'charge_station_cancel', 'charge_station_search', 'charge_station_operators', 'charge_station_by_id', 'charge_station_activity_list', 'nearby_charge_stations', 'charge_station_start', 'charge_station_stop']


@dataclass
class RuntimeData:
    api: GwmAnzClient
    coordinator: GwmAnzCoordinator


def _first_entry(hass: HomeAssistant) -> ConfigEntry:
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise RuntimeError("GWM ANZ is not configured")
    if len(entries) > 1:
        raise RuntimeError("Multiple GWM ANZ entries are configured; provide a VIN for vehicle services")
    return entries[0]


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _vehicle_by_vin(entry: ConfigEntry, vin: str | None) -> dict[str, Any]:
    coord = entry.runtime_data.coordinator
    vehicles = coord.vehicles
    if not vehicles:
        raise RuntimeError("No GWM vehicles loaded yet")
    if not vin:
        return vehicles[0]
    for vehicle in vehicles:
        if vin in {vehicle.get("vin"), vehicle.get("encrypted_vin")}:
            return vehicle
    raise RuntimeError(f"GWM vehicle not found: {vin}")


def _entry_for_vehicle(hass: HomeAssistant, vin: str | None) -> ConfigEntry:
    """Return the config entry that owns a vehicle VIN, or the only entry."""
    if not vin:
        return _first_entry(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not hasattr(entry, "runtime_data"):
            continue
        if entry.runtime_data.coordinator.vehicle(vin):
            return entry
    raise RuntimeError(f"GWM vehicle not found: {vin}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    device_id = entry.data.get(CONF_DEVICE_ID) or uuid.uuid4().hex[:16]
    api = GwmAnzClient(
        session,
        account=entry.data[CONF_ACCOUNT],
        password=entry.data[CONF_PASSWORD],
        country=entry.data.get(CONF_COUNTRY, DEFAULT_COUNTRY),
        device_id=device_id,
        access_token=entry.data.get("access_token"),
        refresh_token=entry.data.get("refresh_token"),
    )
    coord = GwmAnzCoordinator(
        hass,
        api,
        entry.options.get(CONF_POLL_INTERVAL, entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
    )
    coord.config_entry = entry
    entry.runtime_data = RuntimeData(api, coord)
    try:
        await coord.async_config_entry_first_refresh()
    except GwmAnzAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GwmAnzApiError as err:
        raise ConfigEntryNotReady(str(err)) from err
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_DEVICE_ID: device_id, **api.tokens})
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    for service in GWM_ANZ_SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    async def _refresh(call: ServiceCall) -> None:
        await coord.async_request_refresh()

    async def _send_raw_command(call: ServiceCall) -> None:
        target_entry = _entry_for_vehicle(hass, call.data.get("vin"))
        vehicle = _vehicle_by_vin(target_entry, call.data.get("vin"))
        security_password = call.data.get(CONF_SECURITY_PASSWORD) or target_entry.options.get(CONF_SECURITY_PASSWORD) or target_entry.data.get(CONF_SECURITY_PASSWORD)
        instructions = call.data["instructions"]
        if isinstance(instructions, str):
            instructions = json.loads(instructions)
        result = await target_entry.runtime_data.api.async_send_cmd(
            vehicle["encrypted_vin"],
            security_password,
            instructions,
        )
        await target_entry.runtime_data.coordinator.async_record_command(result.vin, f"queued {result.id}")

    def _command_context(call: ServiceCall) -> tuple[ConfigEntry, dict[str, Any], str]:
        target_entry = _entry_for_vehicle(hass, call.data.get("vin"))
        vehicle = _vehicle_by_vin(target_entry, call.data.get("vin"))
        security_password = call.data.get(CONF_SECURITY_PASSWORD) or target_entry.options.get(CONF_SECURITY_PASSWORD) or target_entry.data.get(CONF_SECURITY_PASSWORD)
        return target_entry, vehicle, security_password

    async def _record(target_entry: ConfigEntry, result) -> None:
        await target_entry.runtime_data.coordinator.async_record_command(result.vin, f"queued {result.id}")

    async def _set_windows(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_windows(
            vehicle["encrypted_vin"], sec,
            instruction_key=call.data.get("instruction_key", "0x08"),
            switch_order=call.data.get("switch_order"),
            left_front=call.data.get("left_front"), right_front=call.data.get("right_front"),
            left_back=call.data.get("left_back"), right_back=call.data.get("right_back"),
            sky_light=call.data.get("sky_light"), shade_screen=call.data.get("shade_screen"),
        )
        await _record(target_entry, result)

    async def _search_vehicle(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_search_vehicle(
            vehicle["encrypted_vin"], sec, instruction_key=call.data["instruction_key"],
            flashing=call.data.get("flashing"), whistle=call.data.get("whistle"),
            switch_order=call.data.get("switch_order"), operation_time=call.data.get("operation_time"),
        )
        await _record(target_entry, result)

    async def _set_defrost(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_defrost(
            vehicle["encrypted_vin"], sec, instruction_key=call.data.get("instruction_key", "0x0B"),
            switch_order=call.data.get("switch_order"), operation_time=call.data.get("operation_time"),
            defrost_front=call.data.get("defrost_front"), defrost_back=call.data.get("defrost_back"),
        )
        await _record(target_entry, result)

    async def _set_circulation(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_circulation(
            vehicle["encrypted_vin"], sec, switch_order=call.data["switch_order"],
        )
        await _record(target_entry, result)

    async def _set_seat(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_seat(
            vehicle["encrypted_vin"], sec, instruction_key=call.data.get("instruction_key", "0x0A"),
            switch_order=call.data.get("switch_order"), operation_mode=call.data.get("operation_mode"),
            operation_time=call.data.get("operation_time"), left_front=call.data.get("left_front"),
            right_front=call.data.get("right_front"), left_back=call.data.get("left_back"),
            right_back=call.data.get("right_back"),
        )
        await _record(target_entry, result)

    async def _set_light(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_light(
            vehicle["encrypted_vin"], sec, instruction_key=call.data["instruction_key"],
            headlamp=call.data.get("headlamp"), far_near=call.data.get("far_near"),
            fog_lamps=call.data.get("fog_lamps"), indicator_lamp=call.data.get("indicator_lamp"),
            left_turn=call.data.get("left_turn"), right_turn=call.data.get("right_turn"),
            switch_order=call.data.get("switch_order"), operation_time=call.data.get("operation_time"),
        )
        await _record(target_entry, result)

    async def _set_activation(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_activation(
            vehicle["encrypted_vin"], sec, instruction_key=call.data["instruction_key"],
            function_type=call.data["function_type"], switch_order=call.data["switch_order"],
        )
        await _record(target_entry, result)

    async def _set_idle_charging(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_set_idle_charging(
            vehicle["encrypted_vin"], sec, instruction_key=call.data["instruction_key"],
            idle_charging=call.data["idle_charging"], switch_order=call.data.get("switch_order"),
            operation_time=call.data.get("operation_time"),
        )
        await _record(target_entry, result)

    async def _send_t5_charging_instruction(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        result = await target_entry.runtime_data.api.async_send_t5_charging_instruction(
            vehicle["encrypted_vin"], sec, instruction_key=call.data["instruction_key"],
            start_time=call.data["start_time"], end_time=call.data["end_time"],
            start_soc=call.data["start_soc"], end_soc=call.data["end_soc"],
        )
        await _record(target_entry, result)

    async def _get_vehicle_charging_info(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_get_vehicle_charging_info(vehicle["encrypted_vin"])

    async def _get_vehicle_charge_logs(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_get_vehicle_charge_logs(vehicle["encrypted_vin"], page_num=call.data.get("page_num", 1), page_size=call.data.get("page_size", 20))

    async def _set_vehicle_charging_plan(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_set_vehicle_charging_plan(
            vehicle["encrypted_vin"], enable=call.data["enable"], start_time=call.data["start_time"],
            end_time=call.data["end_time"], weeks=call.data["weeks"], plan_type=call.data.get("plan_type"),
            seq_no=call.data.get("seq_no"),
        )
        await target_entry.runtime_data.coordinator.async_request_refresh()

    async def _query_battery_preheat_plan(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_query_battery_preheat_plan(vehicle["encrypted_vin"])

    async def _set_battery_preheat_plan(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_set_battery_preheat_plan(
            vehicle["encrypted_vin"], switch_order=call.data["switch_order"], start_time=call.data["start_time"], seq_no=call.data.get("seq_no")
        )

    async def _get_compound_templates(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_get_compound_command_template_list(vehicle["encrypted_vin"])

    async def _get_compound_template_info(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_get_compound_command_template_info(vehicle["encrypted_vin"], call.data["template_id"])

    async def _check_security_password(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        sec = call.data.get(CONF_SECURITY_PASSWORD) or target_entry.options.get(CONF_SECURITY_PASSWORD) or target_entry.data.get(CONF_SECURITY_PASSWORD)
        await target_entry.runtime_data.api.async_check_security_password(sec, call.data.get("type", "2"))

    async def _open_sliding_door(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_open_sliding_door(
            vehicle["encrypted_vin"], validity_period=call.data["validity_period"],
            type_=call.data["type"], seq_no=call.data.get("seq_no")
        )

    async def _cancel_sliding_door(call: ServiceCall) -> None:
        target_entry, vehicle, sec = _command_context(call)
        await target_entry.runtime_data.api.async_cancel_sliding_door(vehicle["encrypted_vin"], seq_no=call.data.get("seq_no"))

    async def _charge_station_status(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_status()

    async def _charge_station_info(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_info(call.data["session_id"])

    async def _charge_station_points(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_points(call.data["access_code"], call.data["serial_number"], call.data["type"])

    async def _charge_station_new_points(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_new_points(call.data["access_code"], call.data["serial_number"], call.data["type"])

    async def _charge_station_create_session(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_create_session(call.data["access_code"], call.data["connector_id"], call.data["serial_number"], call.data.get("plural", True))

    async def _charge_station_check_in(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        body = call.data["body"]
        if isinstance(body, str):
            body = json.loads(body)
        await target_entry.runtime_data.api.async_charge_station_check_in(body)

    async def _charge_station_check_out(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_check_out(call.data["access_code"], call.data["serial_number"], call.data["session_id"])

    async def _charge_station_unlock(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_unlock(call.data["access_code"], call.data["connector_id"], call.data["serial_number"], call.data["session_id"], call.data["smart_lock_id"])

    async def _charge_station_cancel(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_cancel(call.data["session_id"])

    async def _charge_station_search(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_search(call.data["longitude"], call.data["latitude"], call.data.get("key_word", ""), call.data.get("page_index", 1), call.data.get("page_size", 20))

    async def _charge_station_operators(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_operators()

    async def _charge_station_by_id(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_by_id(call.data["station_id"], call.data["type"])

    async def _charge_station_activity_list(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_activity_list()

    async def _nearby_charge_stations(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_nearby_charge_stations(call.data["longitude"], call.data["latitude"], call.data.get("types"))

    async def _charge_station_start(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_start(
            call.data["access_code"], call.data["serial_number"], call.data["session_id"]
        )

    async def _charge_station_stop(call: ServiceCall) -> None:
        target_entry = _first_entry(hass)
        await target_entry.runtime_data.api.async_charge_station_stop(
            call.data["access_code"], call.data["serial_number"], call.data["session_id"]
        )

    hass.services.async_register(DOMAIN, "refresh", _refresh)
    hass.services.async_register(
        DOMAIN,
        "send_raw_command",
        _send_raw_command,
        schema=vol.Schema(
            {
                vol.Optional("vin"): str,
                vol.Required("instructions"): vol.Any(dict, str),
                vol.Optional(CONF_SECURITY_PASSWORD): str,
            }
        ),
    )
    opt_text = vol.Any(str, int)
    base_command_schema = {vol.Optional("vin"): str, vol.Optional(CONF_SECURITY_PASSWORD): str}
    hass.services.async_register(DOMAIN, "set_windows", _set_windows, schema=vol.Schema({**base_command_schema, vol.Optional("instruction_key", default="0x08"): str, vol.Optional("switch_order"): opt_text, vol.Optional("left_front"): int, vol.Optional("right_front"): int, vol.Optional("left_back"): int, vol.Optional("right_back"): int, vol.Optional("sky_light"): int, vol.Optional("shade_screen"): int}))
    hass.services.async_register(DOMAIN, "search_vehicle", _search_vehicle, schema=vol.Schema({**base_command_schema, vol.Required("instruction_key"): str, vol.Optional("flashing"): opt_text, vol.Optional("whistle"): opt_text, vol.Optional("switch_order"): opt_text, vol.Optional("operation_time"): opt_text}))
    hass.services.async_register(DOMAIN, "set_defrost", _set_defrost, schema=vol.Schema({**base_command_schema, vol.Optional("instruction_key", default="0x0B"): str, vol.Optional("switch_order"): opt_text, vol.Optional("operation_time"): opt_text, vol.Optional("defrost_front"): opt_text, vol.Optional("defrost_back"): opt_text}))
    hass.services.async_register(DOMAIN, "set_circulation", _set_circulation, schema=vol.Schema({**base_command_schema, vol.Required("switch_order"): opt_text}))
    hass.services.async_register(DOMAIN, "set_seat", _set_seat, schema=vol.Schema({**base_command_schema, vol.Optional("instruction_key", default="0x0A"): str, vol.Optional("switch_order"): opt_text, vol.Optional("operation_mode"): opt_text, vol.Optional("operation_time"): opt_text, vol.Optional("left_front"): opt_text, vol.Optional("right_front"): opt_text, vol.Optional("left_back"): opt_text, vol.Optional("right_back"): opt_text}))
    hass.services.async_register(DOMAIN, "set_light", _set_light, schema=vol.Schema({**base_command_schema, vol.Required("instruction_key"): str, vol.Optional("headlamp"): opt_text, vol.Optional("far_near"): opt_text, vol.Optional("fog_lamps"): opt_text, vol.Optional("indicator_lamp"): opt_text, vol.Optional("left_turn"): opt_text, vol.Optional("right_turn"): opt_text, vol.Optional("switch_order"): opt_text, vol.Optional("operation_time"): opt_text}))
    hass.services.async_register(DOMAIN, "set_activation", _set_activation, schema=vol.Schema({**base_command_schema, vol.Required("instruction_key"): str, vol.Required("function_type"): opt_text, vol.Required("switch_order"): opt_text}))
    hass.services.async_register(DOMAIN, "set_idle_charging", _set_idle_charging, schema=vol.Schema({**base_command_schema, vol.Required("instruction_key"): str, vol.Required("idle_charging"): opt_text, vol.Optional("switch_order"): opt_text, vol.Optional("operation_time"): opt_text}))
    hass.services.async_register(DOMAIN, "send_t5_charging_instruction", _send_t5_charging_instruction, schema=vol.Schema({**base_command_schema, vol.Required("instruction_key"): str, vol.Required("start_time"): int, vol.Required("end_time"): int, vol.Required("start_soc"): int, vol.Required("end_soc"): int}))
    hass.services.async_register(DOMAIN, "get_vehicle_charging_info", _get_vehicle_charging_info, schema=vol.Schema({vol.Optional("vin"): str}))
    hass.services.async_register(DOMAIN, "get_vehicle_charge_logs", _get_vehicle_charge_logs, schema=vol.Schema({vol.Optional("vin"): str, vol.Optional("page_num", default=1): int, vol.Optional("page_size", default=20): int}))
    hass.services.async_register(DOMAIN, "set_vehicle_charging_plan", _set_vehicle_charging_plan, schema=vol.Schema({vol.Optional("vin"): str, vol.Required("enable"): bool, vol.Required("start_time"): str, vol.Required("end_time"): str, vol.Required("weeks"): str, vol.Optional("plan_type"): int, vol.Optional("seq_no"): str}))
    hass.services.async_register(DOMAIN, "query_battery_preheat_plan", _query_battery_preheat_plan, schema=vol.Schema({vol.Optional("vin"): str}))
    hass.services.async_register(DOMAIN, "set_battery_preheat_plan", _set_battery_preheat_plan, schema=vol.Schema({vol.Optional("vin"): str, vol.Required("switch_order"): bool, vol.Required("start_time"): str, vol.Optional("seq_no"): str}))
    hass.services.async_register(DOMAIN, "get_compound_templates", _get_compound_templates, schema=vol.Schema({vol.Optional("vin"): str}))
    hass.services.async_register(DOMAIN, "get_compound_template_info", _get_compound_template_info, schema=vol.Schema({vol.Optional("vin"): str, vol.Required("template_id"): str}))
    hass.services.async_register(DOMAIN, "check_security_password", _check_security_password, schema=vol.Schema({vol.Optional(CONF_SECURITY_PASSWORD): str, vol.Optional("type", default="2"): str}))
    hass.services.async_register(DOMAIN, "open_sliding_door", _open_sliding_door, schema=vol.Schema({vol.Optional("vin"): str, vol.Required("validity_period"): int, vol.Required("type"): int, vol.Optional("seq_no"): str}))
    hass.services.async_register(DOMAIN, "cancel_sliding_door", _cancel_sliding_door, schema=vol.Schema({vol.Optional("vin"): str, vol.Optional("seq_no"): str}))
    hass.services.async_register(DOMAIN, "charge_station_status", _charge_station_status)
    station_session_schema = vol.Schema({vol.Required("access_code"): str, vol.Required("serial_number"): str, vol.Required("session_id"): str})
    hass.services.async_register(DOMAIN, "charge_station_info", _charge_station_info, schema=vol.Schema({vol.Required("session_id"): str}))
    hass.services.async_register(DOMAIN, "charge_station_points", _charge_station_points, schema=vol.Schema({vol.Required("access_code"): str, vol.Required("serial_number"): str, vol.Required("type"): int}))
    hass.services.async_register(DOMAIN, "charge_station_new_points", _charge_station_new_points, schema=vol.Schema({vol.Required("access_code"): str, vol.Required("serial_number"): str, vol.Required("type"): int}))
    hass.services.async_register(DOMAIN, "charge_station_create_session", _charge_station_create_session, schema=vol.Schema({vol.Required("access_code"): str, vol.Required("connector_id"): str, vol.Required("serial_number"): str, vol.Optional("plural", default=True): bool}))
    hass.services.async_register(DOMAIN, "charge_station_check_in", _charge_station_check_in, schema=vol.Schema({vol.Required("body"): vol.Any(dict, str)}))
    hass.services.async_register(DOMAIN, "charge_station_check_out", _charge_station_check_out, schema=station_session_schema)
    hass.services.async_register(DOMAIN, "charge_station_unlock", _charge_station_unlock, schema=vol.Schema({vol.Required("access_code"): str, vol.Required("connector_id"): str, vol.Required("serial_number"): str, vol.Required("session_id"): str, vol.Required("smart_lock_id"): str}))
    hass.services.async_register(DOMAIN, "charge_station_cancel", _charge_station_cancel, schema=vol.Schema({vol.Required("session_id"): str}))
    hass.services.async_register(DOMAIN, "charge_station_search", _charge_station_search, schema=vol.Schema({vol.Required("longitude"): str, vol.Required("latitude"): str, vol.Optional("key_word", default=""): str, vol.Optional("page_index", default=1): int, vol.Optional("page_size", default=20): int}))
    hass.services.async_register(DOMAIN, "charge_station_operators", _charge_station_operators)
    hass.services.async_register(DOMAIN, "charge_station_by_id", _charge_station_by_id, schema=vol.Schema({vol.Required("station_id"): str, vol.Required("type"): int}))
    hass.services.async_register(DOMAIN, "charge_station_activity_list", _charge_station_activity_list)
    hass.services.async_register(DOMAIN, "nearby_charge_stations", _nearby_charge_stations, schema=vol.Schema({vol.Required("longitude"): str, vol.Required("latitude"): str, vol.Optional("types"): str}))
    hass.services.async_register(
        DOMAIN,
        "charge_station_start",
        _charge_station_start,
        schema=vol.Schema(
            {vol.Required("access_code"): str, vol.Required("serial_number"): str, vol.Required("session_id"): str}
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "charge_station_stop",
        _charge_station_stop,
        schema=vol.Schema(
            {vol.Required("access_code"): str, vol.Required("serial_number"): str, vol.Required("session_id"): str}
        ),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        for service in GWM_ANZ_SERVICES:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return unload_ok
