"""Constants for ReliableLight."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "reliable_light"
PLATFORMS: Final = (Platform.LIGHT,)
CONFIG_ENTRY_VERSION: Final = 2

CONF_SOURCES: Final = "source_entity_registry_ids"
CONF_SOURCE: Final = "source_entity_registry_id"
CONF_POWER: Final = "power_entity_registry_id"
SUBENTRY_TYPE_MANAGED_LIGHT: Final = "managed_light"
CONF_RETRY_INITIAL: Final = "retry_initial_seconds"
CONF_RETRY_MAX: Final = "retry_max_seconds"
CONF_VERIFICATION_DELAY: Final = "verification_delay_seconds"
CONF_VERIFICATION_TOLERANCE: Final = "verification_tolerance"
CONF_COMMAND_EXPIRY: Final = "command_expiry_seconds"
CONF_PERSISTENT_RETRY: Final = "persistent_retry"
CONF_DIAGNOSTIC_ATTRIBUTES: Final = "diagnostic_attributes"
CONF_EMIT_EVENTS: Final = "emit_diagnostic_events"

DEFAULT_RETRY_INITIAL: Final = 2.0
DEFAULT_RETRY_MAX: Final = 60.0
DEFAULT_VERIFICATION_DELAY: Final = 1.0
DEFAULT_VERIFICATION_TOLERANCE: Final = "normal"
DEFAULT_COMMAND_EXPIRY: Final = 0
DEFAULT_PERSISTENT_RETRY: Final = False
DEFAULT_DIAGNOSTIC_ATTRIBUTES: Final = True
DEFAULT_EMIT_EVENTS: Final = False

MIN_PERSISTENT_EXPIRY: Final = 30
MAX_COMMAND_EXPIRY: Final = 86400
BACKOFF_MULTIPLIER: Final = 2.0
BACKOFF_JITTER: Final = 0.2

EVENT_COMMAND_STATUS: Final = "reliable_light_command_status"
STORAGE_VERSION: Final = 1

ATTR_SOURCE_ENTITY_ID: Final = "source_entity_id"
ATTR_SOURCE_STATE: Final = "source_state"
ATTR_SOURCE_AVAILABLE: Final = "source_available"
ATTR_POWER_CONFIGURED: Final = "power_configured"
ATTR_POWER_ENTITY_ID: Final = "power_entity_id"
ATTR_POWER_STATE: Final = "power_state"
ATTR_POWER_AVAILABLE: Final = "power_available"
ATTR_LAST_KNOWN_STATE: Final = "last_known_state"
ATTR_PENDING: Final = "pending"
ATTR_PENDING_ACTION: Final = "pending_action"
ATTR_PENDING_STAGE: Final = "pending_stage"
ATTR_PENDING_SINCE: Final = "pending_since"
ATTR_ATTEMPT_COUNT: Final = "attempt_count"
ATTR_NEXT_RETRY_AT: Final = "next_retry_at"
ATTR_LAST_RESULT: Final = "last_result"
ATTR_LAST_VERIFIED_AT: Final = "last_verified_at"
ATTR_WORKER_FAILED: Final = "worker_failed"
