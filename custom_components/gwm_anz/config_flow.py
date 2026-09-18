"""Config flow for GWM ANZ Cloud."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, TextSelector

from .api import GwmAnzAuthError, GwmAnzClient, GwmAnzVerificationRequired
from .const import (
    CONF_ACCOUNT,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SECURITY_PASSWORD,
    CONF_VERIFY_CODE,
    DEFAULT_COUNTRY,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

COUNTRIES = ["AU", "NZ"]
POLL_INTERVALS = ["15", "30", "60", "120", "300", "600"]

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None, *, require_password: bool = True) -> vol.Schema:
    """Return the initial setup schema.

    vol.In serializes as a dropdown in Home Assistant. Poll interval choices are
    strings because the frontend posts select values back as strings; the flow
    converts the selected value to int before saving.
    """
    defaults = defaults or {}
    account_key = vol.Required(CONF_ACCOUNT, default=defaults[CONF_ACCOUNT]) if CONF_ACCOUNT in defaults else vol.Required(CONF_ACCOUNT)
    password_key = vol.Required(CONF_PASSWORD) if require_password else vol.Optional(CONF_PASSWORD)
    security_key = vol.Optional(CONF_SECURITY_PASSWORD, default=defaults[CONF_SECURITY_PASSWORD]) if defaults.get(CONF_SECURITY_PASSWORD) else vol.Optional(CONF_SECURITY_PASSWORD)
    return vol.Schema(
        {
            account_key: TextSelector({"type": "email"}),
            password_key: TextSelector({"type": "password"}),
            vol.Required(CONF_COUNTRY, default=defaults.get(CONF_COUNTRY, DEFAULT_COUNTRY)): SelectSelector(
                {"options": COUNTRIES, "mode": "dropdown"}
            ),
            security_key: TextSelector({"type": "password"}),
            vol.Optional(CONF_POLL_INTERVAL, default=str(defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))): SelectSelector(
                {"options": POLL_INTERVALS, "mode": "dropdown"}
            ),
        }
    )


def _options_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return options schema for runtime preferences."""
    defaults = defaults or {}
    security_key = vol.Optional(CONF_SECURITY_PASSWORD, default=defaults[CONF_SECURITY_PASSWORD]) if defaults.get(CONF_SECURITY_PASSWORD) else vol.Optional(CONF_SECURITY_PASSWORD)
    return vol.Schema(
        {
            security_key: TextSelector({"type": "password"}),
            vol.Optional(CONF_POLL_INTERVAL, default=str(defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))): SelectSelector(
                {"options": POLL_INTERVALS, "mode": "dropdown"}
            ),
        }
    )


class GwmAnzConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a GWM ANZ config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending_user_input: dict[str, Any] | None = None
        self._pending_update_entry = None

    @staticmethod
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return GwmAnzOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect account credentials and trigger login/SMS if needed."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            data[CONF_POLL_INTERVAL] = int(data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
            await self.async_set_unique_id(data[CONF_ACCOUNT])
            self._abort_if_unique_id_configured()
            data[CONF_DEVICE_ID] = uuid.uuid4().hex[:16]
            self._pending_user_input = data
            self._pending_update_entry = None
            result = await self._try_create_entry(data)
            if result == "verification_required":
                return await self.async_step_verify()
            if isinstance(result, dict):
                return result
            errors["base"] = result

        return self.async_show_form(step_id="user", data_schema=_user_schema(), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle user-triggered reconfiguration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        defaults = {**entry.data, **entry.options}
        if user_input is not None:
            data = {**entry.data, **dict(user_input)}
            data.pop(CONF_VERIFY_CODE, None)
            if not data.get(CONF_PASSWORD):
                data[CONF_PASSWORD] = entry.data[CONF_PASSWORD]
            data[CONF_POLL_INTERVAL] = int(data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
            data[CONF_DEVICE_ID] = entry.data.get(CONF_DEVICE_ID) or uuid.uuid4().hex[:16]
            self._pending_user_input = data
            self._pending_update_entry = entry
            result = await self._try_create_entry(data, update_entry=entry)
            if result == "verification_required":
                return await self.async_step_verify()
            if isinstance(result, dict):
                return result
            errors["base"] = result

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_user_schema(defaults, require_password=False),
            errors=errors,
            description_placeholders={"account": entry.data.get(CONF_ACCOUNT, "unknown")},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle authentication failure."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect updated password and refresh login tokens."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            data.pop(CONF_VERIFY_CODE, None)
            self._pending_user_input = data
            self._pending_update_entry = entry
            result = await self._try_create_entry(data, update_entry=entry)
            if result == "verification_required":
                return await self.async_step_verify()
            if isinstance(result, dict):
                return result
            errors["base"] = result

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): TextSelector({"type": "password"})}),
            errors=errors,
            description_placeholders={"account": entry.data.get(CONF_ACCOUNT, "unknown")},
        )

    async def async_step_verify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the verification code that the first login attempt triggers."""
        errors: dict[str, str] = {}
        if not self._pending_user_input:
            return await self.async_step_user()
        if user_input is not None:
            data = {**self._pending_user_input, CONF_VERIFY_CODE: user_input[CONF_VERIFY_CODE]}
            result = await self._try_create_entry(data, update_entry=self._pending_update_entry)
            if isinstance(result, dict):
                return result
            errors["base"] = result

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required(CONF_VERIFY_CODE): str}),
            errors=errors,
            description_placeholders={"account": self._pending_user_input.get(CONF_ACCOUNT, "unknown")},
        )

    async def _try_create_entry(self, user_input: dict[str, Any], update_entry=None) -> ConfigFlowResult | str:
        """Try login validation. Returns a ConfigFlowResult or an error key."""
        session = async_get_clientsession(self.hass)
        api = GwmAnzClient(
            session,
            account=user_input[CONF_ACCOUNT],
            password=user_input[CONF_PASSWORD],
            country=user_input.get(CONF_COUNTRY, DEFAULT_COUNTRY),
            device_id=user_input[CONF_DEVICE_ID],
        )
        try:
            verify_code = user_input.get(CONF_VERIFY_CODE) or None
            if verify_code:
                await api.async_check_verification_code(verify_code)
            await api.async_login(verify_code)
            data = {key: value for key, value in user_input.items() if key != CONF_VERIFY_CODE}
            data.update(api.tokens)
            if update_entry is not None:
                return self.async_update_reload_and_abort(update_entry, data=data)
            return self.async_create_entry(
                title=f"GWM ANZ {user_input[CONF_ACCOUNT]}",
                data=data,
            )
        except GwmAnzVerificationRequired:
            if user_input.get(CONF_VERIFY_CODE):
                return "invalid_code"
            try:
                await api.async_request_verification_code()
            except Exception as err:
                _LOGGER.warning("GWM ANZ verification is required, but requesting a code failed: %s", err)
            return "verification_required"
        except GwmAnzAuthError:
            return "invalid_auth"
        except Exception as err:
            _LOGGER.exception("Unexpected error during GWM ANZ config flow validation: %s", err)
            return "cannot_connect"


class GwmAnzOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle options for GWM ANZ."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            data = dict(user_input)
            data[CONF_POLL_INTERVAL] = int(data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
            return self.async_create_entry(title="", data=data)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema({**self.config_entry.data, **self.config_entry.options}),
        )
