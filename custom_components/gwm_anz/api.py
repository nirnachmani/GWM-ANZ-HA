"""Async client for GWM ANZ cloud API."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import string
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl

import aiohttp

_LOGGER = logging.getLogger(__name__)

APP_KEY = "6794772965"
APP_SECRET = "bf97d8b243b187c0f7ca627cb1815674"
H5_BASE = "https://aus-h5-gateway.gwmcloud.com/app-api/api/v1.0/"
# The ANZ app can use aus-app-gateway over HTTP/2, but Home Assistant's aiohttp
# client is HTTP/1.1-only and that gateway closes HTTP/1.1 requests before a
# response ("Cannot write to closing transport" / curl empty reply). The AU/NZ
# reference client routes app/H5 calls through aus-h5-gateway, which accepts
# HTTP/1.1 for the same signed /app-api/api/v1.0 paths.
APP_BASE = H5_BASE

class GwmAnzApiError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code

class GwmAnzAuthError(GwmAnzApiError):
    pass

class GwmAnzVerificationRequired(GwmAnzAuthError):
    pass

@dataclass
class CommandResult:
    id: str
    vin: str
    state: str
    status: str
    raw: dict[str, Any]


def _strip_ws(value: str) -> str:
    return "".join(ch for ch in value if not ch.isspace())

def _nonce() -> str:
    return uuid.uuid4().hex[:16].upper()

def _now_ms() -> str:
    return str(int(time.time() * 1000))

def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def _json_dumps(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

class GwmAnzClient:
    """Small direct cloud client based on GWM ANZ 1.0.4 traffic."""

    def __init__(self, session: aiohttp.ClientSession, *, account: str, password: str, country: str = "AU", device_id: str = "", access_token: str | None = None, refresh_token: str | None = None) -> None:
        self._session = session
        self.account = account
        self.password = password
        self.country = (country or "AU").upper()
        self.device_id = device_id or uuid.uuid4().hex[:16]
        self.access_token = access_token or ""
        self.refresh_token = refresh_token or ""
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> dict[str, str]:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token, "device_id": self.device_id}

    def _base_headers(self) -> dict[str, str]:
        return {
            "rs": "2", "terminal": "GW_APP_Haval", "brand": "1", "enterpriseId": "CC01",
            "appId": "1", "channel": "APP", "cVer": "1.0.4", "systemType": "1",
            "language": "en", "country": self.country, "regionCode": self.country, "timeZone": "ACDT",
            "secVersion": "2.0", "deviceId": self.device_id, "iccid": self.device_id,
            "User-Agent": "okhttp/4.11.0", "Accept-Encoding": "gzip",
        }

    def _signed_headers(self, method: str, absolute_path: str, query: list[tuple[str,str]], body_text: str) -> tuple[dict[str,str], str]:
        headers = self._base_headers()
        if self.access_token:
            headers["accessToken"] = self.access_token
        ts, nonce = _now_ms(), _nonce()
        auth = f"bt-auth-appkey:{APP_KEY}bt-auth-nonce:{nonce}bt-auth-timestamp:{ts}"
        method = method.upper()
        if method == "POST":
            params_part = "" if not body_text else "json=" + body_text
            outgoing_query = query
        else:
            # ANZ app 1.0.4 keeps empty query parameters and signs tokens sorted
            # by their original case-sensitive encoded key=value form. The signed
            # parameter names are then lowercased and concatenated with no separator.
            kept = list(query)
            kept.sort(key=lambda kv: f"{kv[0]}={kv[1]}")
            params_part = "".join(f"{k.lower()}={v}" for k, v in kept)
            outgoing_query = kept
        raw = _strip_ws(method + absolute_path + auth + params_part + APP_SECRET)
        headers.update({
            "bt-auth-appkey": APP_KEY,
            "bt-auth-nonce": nonce,
            "bt-auth-timestamp": ts,
            "bt-auth-sign": _sha256_hex(quote(raw, safe="")),
        })
        new_query = urlencode(outgoing_query, doseq=True)
        return headers, new_query

    async def _request(self, method: str, base: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None, extra_headers: dict[str,str] | None = None, retry_auth: bool = True) -> Any:
        params_list = [(k, "" if v is None else str(v)) for k, v in (params or {}).items()]
        body_text = _json_dumps(json_body) if json_body is not None else ""
        abs_path = "/app-api/api/v1.0/" + path.lstrip("/")
        headers, signed_query = self._signed_headers(method, abs_path, params_list, body_text)
        if body_text:
            headers["Content-Type"] = "application/json; charset=UTF-8"
        if extra_headers:
            headers.update(extra_headers)
        url = base + path.lstrip("/")
        if signed_query:
            url += "?" + signed_query
        for attempt in range(3):
            try:
                async with self._session.request(method, url, headers=headers, data=body_text.encode() if body_text else None, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    text = await resp.text()
                break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                if attempt == 2:
                    raise GwmAnzApiError(f"Connection error calling {path}: {err}") from err
                await asyncio.sleep(0.5 * (attempt + 1))
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError as err:
            raise GwmAnzApiError(f"HTTP {resp.status}: non-JSON response {text[:200]}") from err
        code = str(payload.get("code", ""))
        msg = payload.get("description") or payload.get("message") or f"HTTP {resp.status}"
        token_expired = "token" in msg.lower() and "expired" in msg.lower()
        if resp.status in (401, 403) or code in {"401", "403", "604040", "604041", "604042", "607501"} or token_expired:
            if retry_auth:
                # Refresh the token. Deliberately do NOT fall back to a full
                # login here: login requires SMS verification and cannot self-heal
                # in the background, so a transient refresh failure must surface as
                # a retryable error (coordinator keeps polling) rather than a
                # permanent auth failure that stops the coordinator.
                try:
                    if self.refresh_token:
                        await self.async_refresh_token()
                    else:
                        await self.async_login()
                    return await self._request(method, base, path, params=params, json_body=json_body, extra_headers=extra_headers, retry_auth=False)
                except GwmAnzApiError:
                    _LOGGER.debug("Auth retry failed for %s: refresh/login unsuccessful", path)
            raise GwmAnzAuthError(msg, code or None)
        if code and code != "000000":
            msg = payload.get("description") or payload.get("message") or f"GWM API error {code}"
            msg_l = msg.lower()
            if any(token in msg_l for token in ("verify", "verification", "sms", "code")):
                raise GwmAnzVerificationRequired(msg, code)
            raise GwmAnzApiError(msg, code)
        return payload.get("data", payload)

    async def async_login(self, verify_code: str | None = None) -> dict[str, Any]:
        body = {
            "account": self.account, "password": self.password, "agreement": [1, 2],
            "deviceId": self.device_id, "appType": "0", "country": self.country,
            "accountId": None, "uid": None, "smsCode": None, "pushToken": "", "loginEmail": self.account,
        }
        if verify_code:
            # Reference AU/NZ live capture uses verifyCode on the second loginAccount call.
            # The code is checked separately with checkSMSCode where the field is smsCode.
            body["verifyCode"] = verify_code
        data = await self._request("POST", H5_BASE, "userAuth/loginAccount", json_body=body, retry_auth=False)
        self.access_token = data.get("accessToken") or self.access_token
        self.refresh_token = data.get("refreshToken") or self.refresh_token
        if not self.access_token:
            raise GwmAnzVerificationRequired("Login did not return accessToken; SMS/verification may be required")
        return data

    async def async_request_verification_code(self) -> None:
        await self._request("POST", H5_BASE, "userAuth/getSMSCode", json_body={"type": "17", "email": self.account, "accountId": None, "uid": None}, retry_auth=False)

    async def async_check_verification_code(self, verify_code: str) -> None:
        await self._request("POST", H5_BASE, "userAuth/checkSMSCode", json_body={"email": self.account, "smsCode": verify_code, "type": "17"}, retry_auth=False)

    async def async_refresh_token(self) -> None:
        if not self.refresh_token:
            raise GwmAnzAuthError("No refresh token")
        data = await self._request(
            "POST",
            H5_BASE,
            "userAuth/refreshToken",
            json_body={"accessToken": self.access_token, "refreshToken": self.refresh_token, "deviceId": self.device_id},
            retry_auth=False,
        )
        self.access_token = data.get("accessToken") or self.access_token
        self.refresh_token = data.get("refreshToken") or self.refresh_token

    async def async_ensure_login(self) -> None:
        async with self._lock:
            if not self.access_token:
                await self.async_login()

    async def async_get_vehicles(self) -> list[dict[str, Any]]:
        await self.async_ensure_login()
        data = await self._request("GET", APP_BASE, "globalapp/vehicle/acquireVehicles")
        return list(data or [])

    async def async_get_status(self, encrypted_vin: str, model_id: str | int | None = None) -> dict[str, Any]:
        await self.async_ensure_login()
        params = {"vin": encrypted_vin, "seqNo": ""}
        if model_id:
            params["modelId"] = model_id
        data = await self._request("GET", APP_BASE, "vehicle/getLastStatus", params=params, extra_headers={"vin": encrypted_vin})
        if isinstance(data, dict):
            _LOGGER.debug(
                "getLastStatus fields = %r",
                {k: v for k, v in data.items() if k != "items"},
            )
            items = data.get("items")
            if isinstance(items, list):
                _LOGGER.debug(
                    "getLastStatus items = %r",
                    [(i.get("code"), i.get("value")) for i in items if isinstance(i, dict)],
                )
        return data

    async def async_get_basics(self, encrypted_vin: str) -> dict[str, Any]:
        await self.async_ensure_login()
        basics = await self._request(
            "GET",
            APP_BASE,
            "vehicle/vehicleBasicsInfo",
            params={"vin": encrypted_vin, "flag": "true"},
            extra_headers={"vin": encrypted_vin},
        )
        config = basics.get("config") if isinstance(basics, dict) else None
        if isinstance(config, dict):
            _LOGGER.debug("vehicleBasicsInfo.config = %r", config)
        else:
            _LOGGER.debug("vehicleBasicsInfo has no config; top-level keys = %r", list(basics) if isinstance(basics, dict) else basics)
        return basics

    async def async_get_capabilities(self, encrypted_vin: str) -> Any:
        await self.async_ensure_login()
        return await self._request("GET", APP_BASE, "vehicle/findVehicleCapabilityItem", params={"vin": encrypted_vin, "userRole": "1"}, extra_headers={"vin": encrypted_vin})

    async def async_refresh(self, plain_vin_hint: str | None = None) -> dict[str, Any]:
        vehicles = await self.async_get_vehicles()
        snapshots=[]
        for idx, vehicle in enumerate(vehicles):
            encrypted_vin = vehicle.get("vin") or vehicle.get("encryptVin") or vehicle.get("encryptVIN")
            if not encrypted_vin:
                continue
            model_id = vehicle.get("modelId") or vehicle.get("modelCode") or vehicle.get("vModelId")
            if not model_id and len(vehicles) == 1:
                model_id = "23853"  # observed TANK 500 PHEV ANZ app traffic
            status = await self.async_get_status(encrypted_vin, model_id)
            try:
                basics = await self.async_get_basics(encrypted_vin)
            except GwmAnzApiError as err:
                _LOGGER.debug("vehicleBasicsInfo failed: %s", err)
                basics = {}
            try:
                capabilities = await self.async_get_capabilities(encrypted_vin)
            except GwmAnzApiError:
                capabilities = None
            plain_vin = vehicle.get("vinCode") or vehicle.get("vinNo") or vehicle.get("plainVin") or (plain_vin_hint if len(vehicles) == 1 else None)
            snapshots.append(map_snapshot(vehicle, status, basics, capabilities, encrypted_vin, plain_vin))
        return {"vehicles": snapshots, "raw_vehicle_count": len(vehicles), "tokens": self.tokens}

    def _security_password(self, security_password: str) -> str:
        # The app hashes the remote-control security PIN/password before dispatch.
        if not security_password:
            raise GwmAnzApiError("Remote security password/PIN is required")
        value = security_password.strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", value):
            return value.lower()
        return hashlib.md5(value.encode()).hexdigest()

    async def async_send_cmd(self, encrypted_vin: str, security_password: str, instructions: dict[str, Any]) -> CommandResult:
        seq = uuid.uuid4().hex + "1234"
        body = {"instructions": instructions, "remoteType": "0", "securityPassword": self._security_password(security_password), "seqNo": seq, "type": 2, "vin": encrypted_vin}
        data = await self._request("POST", APP_BASE, "vehicle/T5/sendCmd", json_body=body, extra_headers={"vin": encrypted_vin})
        return CommandResult(seq, encrypted_vin, "queued", "queued", data if isinstance(data, dict) else {"data": data})

    async def async_command_status(self, seq_no: str, encrypted_vin: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicle/getRemoteCtrlResultT5", params={"seqNo": seq_no}, extra_headers={"vin": encrypted_vin})

    async def async_persist_remote_ctl_info(self, encrypted_vin: str, fields: dict[str, Any]) -> Any:
        """Persist staged remote-control settings to the cloud config store.

        The app sends one of these (modifyVehicleRemoteCtlInfo) immediately
        before each T5 command so the staged value is stored in
        vehicleBasicsInfo.config, which other app instances read back.
        """
        body = dict(fields)
        body["vin"] = encrypted_vin
        return await self._request("POST", APP_BASE, "vehicle/modifyVehicleRemoteCtlInfo", json_body=body)

    async def async_modify_vehicle_remote_ctl_info(self, encrypted_vin: str, *, temperature: int, minutes: int) -> Any:
        """Backward-compatible A/C-only persist helper (runtime is seconds here)."""
        return await self.async_persist_remote_ctl_info(
            encrypted_vin,
            {"airConditionerTemperature": str(int(temperature)), "airConditionerTime": str(int(minutes) * 60)},
        )

    async def async_lock(self, encrypted_vin: str, security_password: str, lock: bool) -> CommandResult:
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x05": {"operationTime": "0", "switchOrder": "2" if lock else "1"}})

    async def async_close_windows(self, encrypted_vin: str, security_password: str) -> CommandResult:
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x08": {"switchOrder": "0", "window": {"leftFront":"0","leftBack":"0","rightFront":"0","rightBack":"0","skyLight":""}}})

    async def async_set_climate(self, encrypted_vin: str, security_password: str, *, mode: str, temperature: int = 22, minutes: int = 15) -> CommandResult:
        if mode == "off":
            air_conditioner = {"operationTime": "0", "switchOrder": "2"}
        else:
            try:
                await self.async_modify_vehicle_remote_ctl_info(encrypted_vin, temperature=temperature, minutes=minutes)
            except GwmAnzApiError as err:
                _LOGGER.debug("modifyVehicleRemoteCtlInfo failed before A/C command: %s", err)
            air_conditioner = {"operationTime": str(minutes), "switchOrder": "1", "temperature": str(temperature)}
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x04": {"airConditioner": air_conditioner}})


    async def async_search_vehicle(self, encrypted_vin: str, security_password: str, *, instruction_key: str, flashing: str | int | None = None, whistle: str | int | None = None, switch_order: str | int | None = None, operation_time: str | int | None = None) -> CommandResult:
        body: dict[str, Any] = {"search": {}}
        if switch_order is not None: body["switchOrder"] = str(switch_order)
        if operation_time is not None: body["operationTime"] = str(operation_time)
        if flashing is not None: body["search"]["flashing"] = str(flashing)
        if whistle is not None: body["search"]["whistle"] = str(whistle)
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: body})

    async def async_set_windows(self, encrypted_vin: str, security_password: str, *, instruction_key: str = "0x08", switch_order: str | int | None = None, left_front: int | None = None, right_front: int | None = None, left_back: int | None = None, right_back: int | None = None, sky_light: int | None = None, shade_screen: int | None = None) -> CommandResult:
        window: dict[str, Any] = {}
        for key, val in {"leftFront": left_front, "rightFront": right_front, "leftBack": left_back, "rightBack": right_back, "skyLight": sky_light, "shadeScreen": shade_screen}.items():
            if val is not None: window[key] = int(val)
        body: dict[str, Any] = {"window": window}
        if switch_order is not None: body["switchOrder"] = str(switch_order)
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: body})

    async def async_set_circulation(self, encrypted_vin: str, security_password: str, *, switch_order: str | int) -> CommandResult:
        # Live-captured ANZ shape: instruction 0x11 is the air-circulation
        # (air cleaner) toggle with a fixed ~60s runtime. switchOrder 1=on, 2=off.
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x11": {"switchOrder": str(switch_order)}})

    async def async_set_charging(self, encrypted_vin: str, security_password: str, *, switch_order: str | int) -> CommandResult:
        # Live-captured ANZ shape: instruction 0x01 (flat) for charge-now / stop
        # charging. switchOrder 1=start/charge now, 2=stop charging.
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x01": {"switchOrder": str(switch_order)}})

    async def async_set_steering_wheel(self, encrypted_vin: str, security_password: str, *, switch_order: str | int, minutes: int = 15) -> CommandResult:
        # Live-captured ANZ shape: instruction 0x19 (flat) for heated steering
        # wheel. Persist steeringWheelHeatingTime (seconds) before the T5 command
        # so other app instances see the staged value; on OFF persist "0" so the
        # shared config reflects off (this is how instances sync).
        try:
            await self.async_persist_remote_ctl_info(
                encrypted_vin,
                {"steeringWheelHeatingTime": str(int(minutes) * 60) if switch_order in (None, "1", 1) else "0"},
            )
        except GwmAnzApiError as err:
            _LOGGER.debug("persist steering wheel time failed: %s", err)
        op_time = str(int(minutes)) if switch_order in (None, "1", 1) else "0"
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x19": {"operationTime": op_time, "switchOrder": str(switch_order)}})

    async def async_start_vehicle(self, encrypted_vin: str, security_password: str, *, switch_order: str | int, minutes: int = 10) -> CommandResult:
        # Live-captured ANZ shape: instruction 0x03 (flat) for remote engine
        # start/stop. Persist engineStatusTime (seconds) before the T5 command.
        if switch_order in (None, "1", 1):
            try:
                await self.async_persist_remote_ctl_info(encrypted_vin, {"engineStatusTime": str(int(minutes) * 60)})
            except GwmAnzApiError as err:
                _LOGGER.debug("persist engine status time failed: %s", err)
        op_time = str(int(minutes)) if switch_order in (None, "1", 1) else "0"
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x03": {"operationTime": op_time, "switchOrder": str(switch_order)}})

    async def async_close_sunroof(self, encrypted_vin: str, security_password: str) -> CommandResult:
        # Sunroof close uses the 0x08 window instruction with the skyLight field
        # (confirmed from wd/z.smali; the app's roof quick-control is a no-op when
        # already closed, so the exact value is inferred from the reference
        # window-close shape — skyLight "0" = closed).
        return await self.async_send_cmd(encrypted_vin, security_password, {"0x08": {"switchOrder": "0", "window": {"skyLight": "0"}}})

    async def async_set_defrost(self, encrypted_vin: str, security_password: str, *, instruction_key: str = "0x0B", switch_order: str | int | None = None, operation_time: str | int | None = None, defrost_front: str | int | None = None, defrost_back: str | int | None = None) -> CommandResult:
        # Live-captured ANZ shape: instruction 0x0B wraps a single "defrost"
        # object carrying defrostFront/defrostBack, the runtime in minutes,
        # and switchOrder (1=on, 2=off) together inside it. Before the T5
        # command the app persists the duration (seconds) via
        # modifyVehicleRemoteCtlInfo so other app instances see the staged value.
        if operation_time is not None:
            fields: dict[str, Any] = {}
            if defrost_front is not None:
                fields["frontDefrostTime"] = str(int(operation_time) * 60)
            if defrost_back is not None:
                fields["rearDefrostTime"] = str(int(operation_time) * 60)
            if fields:
                try:
                    await self.async_persist_remote_ctl_info(encrypted_vin, fields)
                except GwmAnzApiError as err:
                    _LOGGER.debug("persist defrost settings failed before T5 command: %s", err)
        defrost: dict[str, Any] = {}
        if defrost_front is not None: defrost["defrostFront"] = str(defrost_front)
        if defrost_back is not None: defrost["defrostBack"] = str(defrost_back)
        if switch_order is not None: defrost["switchOrder"] = str(switch_order)
        if operation_time is not None: defrost["operationTime"] = str(operation_time)
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: {"defrost": defrost}})

    async def async_set_seat(self, encrypted_vin: str, security_password: str, *, instruction_key: str = "0x0A", switch_order: str | int | None = None, operation_mode: str | int | None = None, operation_time: str | int | None = None, left_front: str | int | None = None, right_front: str | int | None = None, left_back: str | int | None = None, right_back: str | int | None = None) -> CommandResult:
        # Live-captured ANZ shape: instruction 0x0A wraps a "seat" object.
        # operationMode "1"=heat, "2"=ventilation (ComfortSeatBean constants).
        # Per-seat levels are 0-9; operationTime is minutes; switchOrder 1=on, 2=off.
        # Before the T5 command the app persists the staged seat settings
        # (leftFrontSeat/rightFrontSeat/leftBackSeat/rightBackSeat,
        # seatHeatingType, seatHeatingControlTime-in-seconds) so other app
        # instances see the same staged values via vehicleBasicsInfo.config.
        if switch_order in (None, "1", 1):
            fields: dict[str, Any] = {
                "leftFrontSeat": str(int(left_front)) if left_front is not None else "0",
                "rightFrontSeat": str(int(right_front)) if right_front is not None else "0",
                "leftBackSeat": str(int(left_back)) if left_back is not None else "0",
                "rightBackSeat": str(int(right_back)) if right_back is not None else "0",
                "leftThirdRowSeat": "0",
                "rightThirdRowSeat": "0",
                "seatHeatingType": str(operation_mode) if operation_mode is not None else "1",
                "seatHeatingControlTime": str(int(operation_time) * 60) if operation_time is not None else "0",
            }
            try:
                await self.async_persist_remote_ctl_info(encrypted_vin, fields)
            except GwmAnzApiError as err:
                _LOGGER.debug("persist seat settings failed before T5 command: %s", err)
        else:
            # OFF: reset the staged seat config so the shared store (and other
            # app instances) reflect the seat as off rather than keeping the
            # previously-persisted level/time.
            fields: dict[str, Any] = {
                "leftFrontSeat": "0", "rightFrontSeat": "0", "leftBackSeat": "0", "rightBackSeat": "0",
                "leftThirdRowSeat": "0", "rightThirdRowSeat": "0",
                "seatHeatingType": "1", "seatHeatingControlTime": "0",
            }
            try:
                await self.async_persist_remote_ctl_info(encrypted_vin, fields)
            except GwmAnzApiError as err:
                _LOGGER.debug("persist seat reset failed before T5 command: %s", err)
        seat: dict[str, Any] = {}
        for key, val in {"leftFront": left_front, "rightFront": right_front, "leftBack": left_back, "rightBack": right_back, "operationMode": operation_mode, "operationTime": operation_time, "switchOrder": switch_order}.items():
            if val is not None: seat[key] = str(val)
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: {"seat": seat}})

    async def async_set_light(self, encrypted_vin: str, security_password: str, *, instruction_key: str, headlamp: str | int | None = None, far_near: str | int | None = None, fog_lamps: str | int | None = None, indicator_lamp: str | int | None = None, left_turn: str | int | None = None, right_turn: str | int | None = None, switch_order: str | int | None = None, operation_time: str | int | None = None) -> CommandResult:
        light: dict[str, Any] = {}
        for key, val in {"headlamp": headlamp, "farNear": far_near, "fogLamps": fog_lamps, "indicatorLamp": indicator_lamp, "leftTurn": left_turn, "rightTurn": right_turn}.items():
            if val is not None: light[key] = str(val)
        body: dict[str, Any] = {"light": light}
        if switch_order is not None: body["switchOrder"] = str(switch_order)
        if operation_time is not None: body["operationTime"] = str(operation_time)
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: body})

    async def async_set_activation(self, encrypted_vin: str, security_password: str, *, instruction_key: str, function_type: str | int, switch_order: str | int) -> CommandResult:
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: {"activation": {"functionType": str(function_type), "switchOrder": str(switch_order)}}})

    async def async_set_idle_charging(self, encrypted_vin: str, security_password: str, *, instruction_key: str, idle_charging: str | int, switch_order: str | int | None = None, operation_time: str | int | None = None) -> CommandResult:
        body: dict[str, Any] = {"idleCharging": str(idle_charging)}
        if switch_order is not None: body["switchOrder"] = str(switch_order)
        if operation_time is not None: body["operationTime"] = str(operation_time)
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: body})

    async def async_send_t5_charging_instruction(self, encrypted_vin: str, security_password: str, *, instruction_key: str, start_time: int, end_time: int, start_soc: int, end_soc: int) -> CommandResult:
        # Payload model confirmed from APK CmdBody.Charging. Function/key semantics are not confirmed.
        return await self.async_send_cmd(encrypted_vin, security_password, {instruction_key: {"charging": {"startTime": int(start_time), "endTime": int(end_time), "startSoc": int(start_soc), "endSoc": int(end_soc)}}})

    async def async_get_vehicle_charging_info(self, encrypted_vin: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicleCharge/getChargingInfos", params={"vin": encrypted_vin}, extra_headers={"vin": encrypted_vin})

    async def async_get_vehicle_charge_logs(self, encrypted_vin: str, *, page_num: int = 1, page_size: int = 20) -> Any:
        return await self._request("POST", APP_BASE, "vehicleCharge/getChargeLogs", json_body={"vin": encrypted_vin, "pageNum": int(page_num), "pageSize": int(page_size)}, extra_headers={"vin": encrypted_vin})

    async def async_set_vehicle_charging_plan(self, encrypted_vin: str, *, enable: bool, start_time: str, end_time: str, weeks: str, plan_type: int | None = None, seq_no: str | None = None) -> Any:
        body: dict[str, Any] = {"vin": encrypted_vin, "enable": bool(enable), "startTime": start_time, "endTime": end_time, "weeks": weeks, "seqNo": seq_no or (uuid.uuid4().hex + "1234")}
        if plan_type is not None:
            body["planType"] = int(plan_type)
        return await self._request("POST", APP_BASE, "vehicleCharge/setChargingPlan", json_body=body, extra_headers={"vin": encrypted_vin})

    async def async_query_battery_preheat_plan(self, encrypted_vin: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicleBatPack/queryBatPackPreheatPlan", params={"vin": encrypted_vin}, extra_headers={"vin": encrypted_vin})

    async def async_set_battery_preheat_plan(self, encrypted_vin: str, *, switch_order: bool, start_time: str, seq_no: str | None = None) -> Any:
        body = {"vin": encrypted_vin, "switchOrder": bool(switch_order), "startTime": start_time, "seqNo": seq_no or (uuid.uuid4().hex + "1234")}
        return await self._request("POST", APP_BASE, "vehicleBatPack/setBatPackPreheatPlan", json_body=body, extra_headers={"vin": encrypted_vin})

    async def async_get_compound_command_template_list(self, encrypted_vin: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicle/getCompoundCommandTemplateList", params={"vin": encrypted_vin}, extra_headers={"vin": encrypted_vin})

    async def async_get_compound_command_template_info(self, encrypted_vin: str, template_id: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicle/getCompoundCommandTemplateInfo", params={"vin": encrypted_vin, "templateId": template_id}, extra_headers={"vin": encrypted_vin})

    async def async_check_security_password(self, security_password: str, type_: str = "2") -> Any:
        return await self._request("POST", APP_BASE, "userAuth/checkSecurityPassword", json_body={"securityPassword": self._security_password(security_password), "type": type_})

    async def async_open_sliding_door(self, encrypted_vin: str, *, validity_period: int, type_: int, seq_no: str | None = None) -> Any:
        body = {"validityPeriod": int(validity_period), "type": int(type_), "vin": encrypted_vin, "seqNo": seq_no or (uuid.uuid4().hex + "1234")}
        return await self._request("POST", APP_BASE, "slideDoor/openSlideDoor", json_body=body, extra_headers={"vin": encrypted_vin})

    async def async_cancel_sliding_door(self, encrypted_vin: str, *, seq_no: str | None = None) -> Any:
        body = {"seqNo": seq_no or (uuid.uuid4().hex + "1234"), "vin": encrypted_vin}
        return await self._request("POST", APP_BASE, "slideDoor/cancelSlideDoor", json_body=body, extra_headers={"vin": encrypted_vin})

    async def async_find_vehicle_body_image(self, encrypted_vin: str, channel_id: str = "2") -> Any:
        return await self._request("GET", APP_BASE, "vehicle/getVehicleBodyImageByVin", params={"vin": encrypted_vin, "channelId": channel_id}, extra_headers={"vin": encrypted_vin})

    async def async_query_service_exp(self, encrypted_vin: str) -> Any:
        return await self._request("POST", H5_BASE, "manage/queryServiceExp", json_body={"vin": encrypted_vin}, extra_headers={"vin": encrypted_vin})

    async def async_charge_station_status(self) -> Any:
        return await self._request("GET", APP_BASE, "vehicleGwmChargeStation/getChargingStatus")

    async def async_charge_station_info(self, session_id: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicleGwmChargeStation/getChargingInfo", params={"sessionId": session_id})

    async def async_charge_station_start(self, access_code: str, serial_number: str, session_id: str) -> Any:
        return await self._request("POST", APP_BASE, "vehicleGwmChargeStation/StartCharge", json_body={"accessCode": access_code, "serialNumber": serial_number, "sessionId": session_id})

    async def async_charge_station_stop(self, access_code: str, serial_number: str, session_id: str) -> Any:
        return await self._request("POST", APP_BASE, "vehicleGwmChargeStation/stopCharge", json_body={"accessCode": access_code, "serialNumber": serial_number, "sessionId": session_id})

    async def async_charge_station_points(self, access_code: str, serial_number: str, type_: int) -> Any:
        return await self._request("GET", APP_BASE, "vehicleGwmChargeStation/getChargingPoints", params={"accessCode": access_code, "serialNumber": serial_number, "type": int(type_)})

    async def async_charge_station_new_points(self, access_code: str, serial_number: str, type_: int) -> Any:
        return await self._request("GET", APP_BASE, "vehicleGwmChargeStation/getNewChargingPoints", params={"accessCode": access_code, "serialNumber": serial_number, "type": int(type_)})

    async def async_charge_station_create_session(self, access_code: str, connector_id: str, serial_number: str, plural: bool = True) -> Any:
        path = "vehicleGwmChargeStation/createChargingSessions" if plural else "vehicleGwmChargeStation/createChargingSession"
        return await self._request("POST", APP_BASE, path, json_body={"accessCode": access_code, "connectorId": connector_id, "serialNumber": serial_number})

    async def async_charge_station_check_in(self, body: dict[str, Any]) -> Any:
        return await self._request("POST", APP_BASE, "vehicleGwmChargeStation/chargingCheckIn", json_body=body)

    async def async_charge_station_check_out(self, access_code: str, serial_number: str, session_id: str) -> Any:
        return await self._request("POST", APP_BASE, "vehicleGwmChargeStation/chargingCheckOut", json_body={"accessCode": access_code, "serialNumber": serial_number, "sessionId": session_id})

    async def async_charge_station_unlock(self, access_code: str, connector_id: str, serial_number: str, session_id: str, smart_lock_id: str) -> Any:
        return await self._request("POST", APP_BASE, "vehicleGwmChargeStation/smartLocks/unlock", json_body={"accessCode": access_code, "connectorId": connector_id, "serialNumber": serial_number, "sessionId": session_id, "smartLockId": smart_lock_id})

    async def async_charge_station_cancel(self, session_id: str) -> Any:
        return await self._request("GET", APP_BASE, "vehicleGwmChargeStation/cancel", params={"sessionId": session_id})

    async def async_charge_station_search(self, longitude: str, latitude: str, key_word: str = "", page_index: int = 1, page_size: int = 20) -> Any:
        return await self._request("GET", APP_BASE, "vehicleChargeStation/getChargeStation", params={"longitude": longitude, "latitude": latitude, "keyWord": key_word, "pageIndex": int(page_index), "pageSize": int(page_size)})

    async def async_charge_station_operators(self) -> Any:
        return await self._request("GET", APP_BASE, "vehicleChargeStation/getAllOperators")

    async def async_charge_station_by_id(self, station_id: str, type_: int) -> Any:
        return await self._request("GET", APP_BASE, "vehicleChargeStation/getChargeStationById", params={"stationId": station_id, "type": int(type_)})

    async def async_charge_station_activity_list(self) -> Any:
        return await self._request("GET", APP_BASE, "vehicleChargeStation/activity/online/list")

    async def async_nearby_charge_stations(self, longitude: str, latitude: str, types: str | None = None) -> Any:
        params = {"longitude": longitude, "latitude": latitude}
        if types is not None:
            params["types"] = types
        return await self._request("GET", APP_BASE, "vehicleChargeStation/getNearbyChargeStation", params=params)


def _item_map(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(i.get("code")): i for i in status.get("items") or [] if i.get("code") is not None}

def _val(items: dict[str, dict[str,Any]], code: str) -> Any:
    item = items.get(code) or {}
    return item.get("value")

def _num(items: dict[str, dict[str,Any]], code: str) -> float | None:
    v = _val(items, code)
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None

def _bool01(items: dict[str,dict[str,Any]], code: str) -> bool | None:
    v = str(_val(items, code)) if _val(items, code) is not None else None
    return True if v == "1" else False if v == "0" else None

# Seat/steering-wheel live state codes (getLastStatus / MQTT dde-status). These
# carry the ACTUAL current state: "0" = off, level (1-9) = on. They are the same
# codes in both stores (confirmed identical), and are NOT the staged config
# (leftFrontSeat etc.), which holds the remembered *preset* level.
_SEAT_STATE_CODES = (
    "2220001", "2220002",  # front seat HEAT (right/left, RHD-swapped)
    "2220003", "2220004",  # front seat VENT
    "2424001", "2424002",  # rear seat HEAT
    "2220018", "2220021",  # rear seat VENT
)
_STEERING_WHEEL_STATE_CODE = "2060016"

def _nonzero_bool(items: dict[str,dict[str,Any]], code: str) -> bool | None:
    """Return True/False/None from a status value where non-zero means on.

    Values may be numeric or strings; invalid markers ("-", "--", "- -", empty)
    mean off (mirrors the app's ``checkValueZero``). ``None`` means the code is
    absent from the payload (state unknown).
    """
    v = _val(items, code)
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "0", "-", "--", "- -", "off", "false", "null", "None"):
        return False
    try:
        return float(s) != 0
    except (TypeError, ValueError):
        return None

def _seat_active(items: dict[str,dict[str,Any]]) -> bool | None:
    """True if any seat heat/vent status code is on, False if all are off."""
    vals = [_nonzero_bool(items, c) for c in _SEAT_STATE_CODES]
    if any(v is True for v in vals):
        return True
    if all(v is False for v in vals):
        return False
    return None

def _temperature_value(value: Any) -> float | None:
    """Return a plausible Celsius value from app status/config fields."""
    num = _float(value)
    if num is None:
        return None
    # Status values are sometimes tenths of a degree (234 => 23.4C), while
    # saved A/C setpoints are usually whole Celsius values (23).
    return num / 10.0 if abs(num) > 80 else num

def _charging_status(items: dict[str,dict[str,Any]]) -> str | None:
    charge = str(_val(items, "2041142")) if _val(items,"2041142") is not None else None
    plugged = _bool01(items, "2042082")
    if charge == "0" and plugged is False: return "disconnected"
    if charge == "0" and plugged is True: return "connected"
    return {"1":"charging", "2":"awaiting_charging", "3":"awaiting_charging", "5":"waiting_for_power", "6":"error"}.get(charge)

def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

def _ts(ms: Any) -> str | None:
    try:
        val = int(ms)
        if val <= 0: return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(val/1000))
    except Exception:
        return None

def _config_minutes(config: dict[str, Any], key: str) -> int | None:
    """Return a config field stored in seconds as whole minutes."""
    v = _float(config.get(key))
    return int(v / 60) if v is not None else None

def map_snapshot(vehicle: dict[str,Any], status: dict[str,Any], basics: dict[str,Any], capabilities: Any, encrypted_vin: str, plain_vin: str | None) -> dict[str,Any]:
    items = _item_map(status)
    values = {
        "soc": _num(items,"2013021"), "range_km": _num(items,"2011501"), "fuel_range_km": _num(items,"2011007"), "remaining_charging_time_min": _num(items,"2013022"), "charge_mode": str(_val(items,"2013023")) if _val(items,"2013023") is not None else None,
        "charging_status": _charging_status(items), "charging_active": str(_val(items,"2041142")) == "1" if _val(items,"2041142") is not None else None,
        "charge_plug_connected": _bool01(items,"2042082"), "odometer_km": _num(items,"2103010"),
        "tire_pressure_front_left_kpa": _num(items,"2101001"), "tire_pressure_front_right_kpa": _num(items,"2101002"), "tire_pressure_rear_left_kpa": _num(items,"2101003"), "tire_pressure_rear_right_kpa": _num(items,"2101004"),
        "tire_temperature_front_left_c": _num(items,"2101005"), "tire_temperature_front_right_c": _num(items,"2101006"), "tire_temperature_rear_left_c": _num(items,"2101007"), "tire_temperature_rear_right_c": _num(items,"2101008"),
        "ac_active": _bool01(items,"2202001"), "locked": (str(_val(items,"2208001")) == "0") if _val(items,"2208001") is not None else None,
        "window_front_left_open": (str(_val(items,"2210001")) != "1") if _val(items,"2210001") is not None else None,
        "window_front_right_open": (str(_val(items,"2210002")) != "1") if _val(items,"2210002") is not None else None,
        "window_rear_left_open": (str(_val(items,"2210003")) != "1") if _val(items,"2210003") is not None else None,
        "window_rear_right_open": (str(_val(items,"2210004")) != "1") if _val(items,"2210004") is not None else None,
        # Air circulation ("chairClear" 车净化, command 0x11): "1"=on, "0"=off.
        "circulation_active": _bool01(items, "2078020"),
        # Front/rear demisting live state (getLastStatus): "1"=on, "0"=off.
        "front_defrost_active": _bool01(items, "2222001"),
        "rear_defrost_active": _bool01(items, "2210032"),
        # Remote engine start state (hybrid engine status).
        "engine_running": str(status.get("hyEngSts")) == "1" if status.get("hyEngSts") is not None else None,
        # Seat climate + heated steering wheel live state (getLastStatus == MQTT).
        # These are the ACTUAL current state (level 1-9 = on), NOT the staged
        # config preset (leftFrontSeat etc.), which stays non-zero while off.
        "seat_active": _seat_active(items),
        "steering_wheel_active": _nonzero_bool(items, _STEERING_WHEEL_STATE_CODE),
    }
    config = basics.get("config") or basics.get("Config") or {}
    name = vehicle.get("appShowSeriesName") or vehicle.get("vehicleNick") or vehicle.get("modelName") or "GWM ANZ"
    return {
        "vin": encrypted_vin, "encrypted_vin": encrypted_vin, "plain_vin": plain_vin, "name": name,
        "manufacturer": vehicle.get("brandName") or vehicle.get("otBrandName") or "GWM",
        "model": vehicle.get("vtype") or vehicle.get("vTypeName") or vehicle.get("modelName"),
        "serial_number": status.get("deviceId"),
        "location": {"latitude": _float(status.get("latitude")), "longitude": _float(status.get("longitude"))} if _float(status.get("latitude")) is not None and _float(status.get("longitude")) is not None else None,
        "timestamps": {"acquisition_time": _ts(status.get("acquisitionTime")), "update_time": _ts(status.get("updateTime")), "last_refresh": _ts(int(time.time()*1000))},
        "capabilities": {"remote_commands": bool(capabilities)}, "values": values,
        "climate": {
            "mode": "on" if values.get("ac_active") else "off",
            "ac_temperature_c": _temperature_value(config.get("airConditionerTemperature")),
            "operation_time_minutes": int(int(config.get("airConditionerTime") or config.get("airConditionerStatusTime") or 900) / 60),
        },
        # Staged remote-control settings persisted via modifyVehicleRemoteCtlInfo
        # and read back via vehicleBasicsInfo.config. This is the shared store
        # that other app instances display (unlike getLastStatus, which is the
        # vehicle's actual telemetry state).
        "remote_settings": {
            "seat_heating_type": config.get("seatHeatingType"),
            "seat_control_time_minutes": _config_minutes(config, "seatHeatingControlTime"),
            "seat_front_left": _float(config.get("leftFrontSeat")),
            "seat_front_right": _float(config.get("rightFrontSeat")),
            "seat_rear_left": _float(config.get("leftBackSeat")),
            "seat_rear_right": _float(config.get("rightBackSeat")),
            "front_defrost_time_minutes": _config_minutes(config, "frontDefrostTime"),
            "rear_defrost_time_minutes": _config_minutes(config, "rearDefrostTime"),
            "steering_wheel_time_minutes": _config_minutes(config, "steeringWheelHeatingTime"),
            "engine_time_minutes": _config_minutes(config, "engineStatusTime"),
        },
        "raw_items": {k: {"value": str(v.get("value")), "unit": v.get("unit")} for k,v in items.items()}, "raw_vehicle": vehicle,
    }
