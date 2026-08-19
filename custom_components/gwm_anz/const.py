"""Constants for GWM ANZ Cloud."""
from __future__ import annotations

DOMAIN = "gwm_anz"
PLATFORMS = ["sensor", "binary_sensor", "device_tracker", "lock", "button", "climate", "number", "switch", "select"]

CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_COUNTRY = "country"
CONF_DEVICE_ID = "device_id"
CONF_SECURITY_PASSWORD = "security_password"
CONF_POLL_INTERVAL = "poll_interval"
CONF_VERIFY_CODE = "verify_code"

DEFAULT_COUNTRY = "AU"
DEFAULT_POLL_INTERVAL = 60

ATTR_RAW_ITEMS = "raw_items"
