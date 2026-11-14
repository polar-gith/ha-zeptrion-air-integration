from __future__ import annotations

"""Constants for zeptrion_air."""

from logging import Logger, getLogger

from homeassistant.const import Platform

LOGGER: Logger = getLogger(__package__)

DOMAIN: str = "zeptrion_air"

CONF_NAME: str ="name"
CONF_IP_ADDRESS: str = "ip_address"
CONF_PORT: str = "port"
CONF_HOSTNAME: str = "hostname"
CONF_TYPE: str = "type"
CONF_FIRMWARE: str = "firmware"
CONF_STEP_DURATION_MS: str = "step_duration_ms"
# Removed options, dropped from config entries by the migration to 1.2
OBSOLETE_OPTIONS: tuple[str, ...] = ("light_polling",)
CONF_AUTO_TRAVEL_TIMES: str = "auto_travel_times"

# Services
SERVICE_BLIND_RECALL_S1: str = "blind_recall_s1"
SERVICE_BLIND_RECALL_S2: str = "blind_recall_s2"
SERVICE_BLIND_RECALL_S3: str = "blind_recall_s3"
SERVICE_BLIND_RECALL_S4: str = "blind_recall_s4"
SERVICE_BLIND_STORE_SCENE: str = "blind_store_scene"
SERVICE_BLIND_CALIBRATE: str = "blind_calibrate"
SERVICE_LIGHT_STORE_SCENE: str = "light_store_scene"
SERVICE_LIGHT_RECALL_SCENE: str = "light_recall_scene"

# Default values
DEFAULT_STEP_DURATION_MS: int = 250
DEFAULT_LIGHT_POLLING: bool = False
DEFAULT_AUTO_TRAVEL_TIMES: bool = True

# Validation values
MIN_STEP_DURATION_MS: int = 100
MAX_STEP_DURATION_MS: int = 32000

# Platforms
PLATFORMS: list[Platform] = [
    Platform.COVER,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.LIGHT,
    Platform.EVENT,
]

# Events
ZEPTRION_AIR_WEBSOCKET_MESSAGE: str = "zeptrion_air_websocket_message"

# Connection handling (see docs/protocol.md of diaznet/zeptrionair-api, section 5):
# the zApp sends nothing while idle and does not answer WebSocket pings, so a
# cheap HTTP request checks that it is still there after this much silence.
LIVENESS_INTERVAL_S: float = 60.0
# Entities only become unavailable after the event stream has been down this long.
UNAVAILABLE_GRACE_S: float = 60.0
# Reconnect backoff with +/-20 % jitter, so 17 zApps do not reconnect in lockstep.
RECONNECT_BACKOFF_MIN_S: float = 1.0
RECONNECT_BACKOFF_MAX_S: float = 60.0
# The zApp web server is small: at most this many HTTP requests per device at once.
MAX_PARALLEL_REQUESTS: int = 2

# Dispatcher signals (formatted with the config entry id)
SIGNAL_AVAILABILITY: str = "zeptrion_air_availability_{}"
SIGNAL_KEY_EVENT: str = "zeptrion_air_key_event_{}"
SIGNAL_TRAVEL_TIMES: str = "zeptrion_air_travel_times_{}"

# Smartfront key events
EVENT_PRESS: str = "press"
EVENT_RELEASE: str = "release"
MAX_SMARTFRONT_KEYS: int = 8

# Blind travel times (options, seconds; 0 / missing = unknown)
MAX_CHANNELS: int = 4


def travel_option_key(direction: str, channel: int) -> str:
    """Option key of a blind's travel time, e.g. ``travel_up_ch1``."""
    return f"travel_{direction}_ch{channel}"


def tilt_option_key(channel: int) -> str:
    """Option key of a blind's slat tilt time, e.g. ``tilt_time_ch1``."""
    return f"tilt_time_ch{channel}"


def is_travel_option(key: str) -> bool:
    """True for the per-blind time options (changed without a reload)."""
    return key.startswith(("travel_up_ch", "travel_down_ch", "tilt_time_ch"))
