"""Data models for ReliableLight."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_COMMAND_EXPIRY,
    CONF_DIAGNOSTIC_ATTRIBUTES,
    CONF_EMIT_EVENTS,
    CONF_PERSISTENT_RETRY,
    CONF_POWER,
    CONF_POWER_RECOVERY_DELAY,
    CONF_RETRY_INITIAL,
    CONF_RETRY_MAX,
    CONF_SOURCE,
    CONF_SOURCES,
    CONF_VERIFICATION_DELAY,
    CONF_VERIFICATION_TOLERANCE,
    CONFIG_ENTRY_VERSION,
    DEFAULT_COMMAND_EXPIRY,
    DEFAULT_DIAGNOSTIC_ATTRIBUTES,
    DEFAULT_EMIT_EVENTS,
    DEFAULT_PERSISTENT_RETRY,
    DEFAULT_POWER_RECOVERY_DELAY,
    DEFAULT_RETRY_INITIAL,
    DEFAULT_RETRY_MAX,
    DEFAULT_VERIFICATION_DELAY,
    DEFAULT_VERIFICATION_TOLERANCE,
    SUBENTRY_TYPE_MANAGED_LIGHT,
)

if TYPE_CHECKING:
    from homeassistant.core import Context


@dataclass(frozen=True, slots=True)
class VerificationTolerance:
    """Numeric tolerances used to compare reported light state."""

    brightness: float
    color_temp_kelvin: float
    hue: float
    saturation: float
    color_channel: float
    xy: float


TOLERANCE_PROFILES = {
    "strict": VerificationTolerance(0, 0, 0.5, 0.5, 0, 0.001),
    "normal": VerificationTolerance(3, 100, 3, 3, 5, 0.01),
    "relaxed": VerificationTolerance(8, 250, 8, 8, 12, 0.03),
}


@dataclass(frozen=True, slots=True)
class ReliableLightOptions:
    """Validated runtime options."""

    retry_initial: float
    retry_max: float
    power_recovery_delay: float
    verification_delay: float
    tolerance_name: str
    command_expiry: int
    persistent_retry: bool
    diagnostic_attributes: bool
    emit_events: bool

    @property
    def tolerance(self) -> VerificationTolerance:
        """Return the selected tolerance profile."""
        return TOLERANCE_PROFILES[self.tolerance_name]

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> ReliableLightOptions:
        """Create options from a config entry."""
        data = {**entry.data, **entry.options}
        return cls(
            retry_initial=float(data.get(CONF_RETRY_INITIAL, DEFAULT_RETRY_INITIAL)),
            retry_max=float(data.get(CONF_RETRY_MAX, DEFAULT_RETRY_MAX)),
            power_recovery_delay=float(
                data.get(
                    CONF_POWER_RECOVERY_DELAY,
                    DEFAULT_POWER_RECOVERY_DELAY,
                )
            ),
            verification_delay=float(
                data.get(CONF_VERIFICATION_DELAY, DEFAULT_VERIFICATION_DELAY)
            ),
            tolerance_name=str(
                data.get(
                    CONF_VERIFICATION_TOLERANCE,
                    DEFAULT_VERIFICATION_TOLERANCE,
                )
            ),
            command_expiry=int(data.get(CONF_COMMAND_EXPIRY, DEFAULT_COMMAND_EXPIRY)),
            persistent_retry=bool(
                data.get(CONF_PERSISTENT_RETRY, DEFAULT_PERSISTENT_RETRY)
            ),
            diagnostic_attributes=bool(
                data.get(
                    CONF_DIAGNOSTIC_ATTRIBUTES,
                    DEFAULT_DIAGNOSTIC_ATTRIBUTES,
                )
            ),
            emit_events=bool(data.get(CONF_EMIT_EVENTS, DEFAULT_EMIT_EVENTS)),
        )


@dataclass(frozen=True, slots=True)
class DesiredCommand:
    """Latest desired command for one proxy."""

    generation: int
    action: str
    kwargs: dict[str, Any]
    accepted_at: float
    expires_at: float | None
    attempt_count: int
    revision: int
    context: Context | None
    restored: bool = False
    power_recovery_used: bool = False


@dataclass(frozen=True, slots=True)
class ManagedLightConfig:
    """One managed source and its optional upstream power dependency."""

    subentry_id: str
    source_registry_id: str
    power_registry_id: str | None


@dataclass(slots=True)
class ReliableLightRuntime:
    """Runtime data stored on the config entry."""

    options: ReliableLightOptions
    managed_lights: tuple[ManagedLightConfig, ...]
    entities: dict[str, Any]


type ReliableLightConfigEntry = ConfigEntry[ReliableLightRuntime]


def configured_sources(entry: ConfigEntry) -> tuple[str, ...]:
    """Return the configured source registry UUIDs."""
    managed = configured_managed_lights(entry)
    if entry.version >= CONFIG_ENTRY_VERSION:
        return tuple(item.source_registry_id for item in managed)
    values = entry.options.get(CONF_SOURCES, entry.data.get(CONF_SOURCES, []))
    return tuple(str(value) for value in values)


def configured_managed_lights(entry: ConfigEntry) -> tuple[ManagedLightConfig, ...]:
    """Return authoritative managed-light subentry configuration."""
    return tuple(
        ManagedLightConfig(
            subentry_id=subentry.subentry_id,
            source_registry_id=str(subentry.data[CONF_SOURCE]),
            power_registry_id=(
                str(subentry.data[CONF_POWER])
                if subentry.data.get(CONF_POWER)
                else None
            ),
        )
        for subentry in entry.get_subentries_of_type(SUBENTRY_TYPE_MANAGED_LIGHT)
        if CONF_SOURCE in subentry.data
    )
