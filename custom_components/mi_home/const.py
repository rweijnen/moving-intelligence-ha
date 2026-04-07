"""Constants for the Moving Intelligence integration."""

DOMAIN = "mi_home"

# Session-based API (primary)
SESSION_API_BASE = "https://app.movingintelligence.com"
WEBSOCKET_PATH = "/app/websocket"
REQUEST_TIMEOUT = 30  # seconds
SESSION_API_LOGIN = "rest/v1/account/logindtowithclientandversion"
SESSION_API_IS_LOGGED_IN = "rest/v1/account/isloggedin"
SESSION_API_LOGOUT = "rest/v1/account/logout"
SESSION_API_GET_CONTEXT = "rest/v1/account/get-context"
SESSION_API_LIVE = "rest/v1/live/light/get"
SESSION_API_MIBLOCK_GET = "rest/v1/miblock/get"
SESSION_API_MIBLOCK_BLOCK = "rest/v1/miblock/block"
SESSION_API_MIBLOCK_UNBLOCK = "rest/v1/miblock/unblock"
SESSION_API_ALARM_BLOCK_GET = "rest/v1/alarmblock/get"
SESSION_API_ALARM_BLOCK_SET = "rest/v1/alarmblock/setperiod"
SESSION_API_ALARM_BLOCK_UNSET = "rest/v1/alarmblock/unset"
SESSION_API_ALARM_MESSAGES = "rest/v1/pushalarm/get"
SESSION_API_BATTERY = "rest/v1/entity/getbatteryvoltage"

# Official REST API (optional, HMAC-signed)
REST_API_BASE = "https://api-app.movingintelligence.com"
REST_API_OBJECTS = "/v1/objects"
REST_API_PERSONS = "/v1/persons"
REST_API_TRIP_CLASSIFICATIONS = "/v1/tripclassifications"
REST_API_TRIP_PERIODS = "/v1/tripperiods"
REST_API_DETAILED_TRIPS = "/v1/object/{object_id}/detailedtrips"
REST_API_ODOMETER = "/v1/object/{object_id}/odometer"

# Config keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_JOURNEY_RECORDING = "journey_recording"
CONF_MAX_JOURNEYS = "max_journeys"

# Defaults
DEFAULT_SCAN_INTERVAL = 60  # seconds
DEFAULT_MAX_JOURNEYS = 100
DEFAULT_JOURNEY_RECORDING = True

# Client identification (mimics mobile app)
CLIENT_PLATFORM = "android"
CLIENT_VERSION = "2.2.1"
CLIENT_OS_VERSION = "14"

# Coordinator
COORD_LIVE = "live"
COORD_CONTEXT = "context"
COORD_BATTERY_INTERVAL = 300  # seconds between battery/miblock polls
COORD_FALLBACK_INTERVAL = 300  # slow REST poll when STOMP push is active

# Coordinate scale factor (API returns microdegrees)
COORD_SCALE = 1_000_000

# STOMP topics
STOMP_TOPIC_POSITION_EVENT = "/user/topic/positionEvent"
STOMP_TOPIC_LIVE_ROUTE = "/user/topic/liveRouteEvent"
STOMP_TOPIC_ENTITY_CHANGED = "/user/topic/entityChanged"
STOMP_HEARTBEAT_MS = 4000
STOMP_RECONNECT_DELAY = 5  # seconds

# Platforms
PLATFORMS = ["device_tracker", "sensor", "binary_sensor", "switch"]
