"""The ReliableLight integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    MAX_COMMAND_EXPIRY,
    MIN_PERSISTENT_EXPIRY,
    PLATFORMS,
    STORAGE_VERSION,
)
from .model import (
    ReliableLightConfigEntry,
    ReliableLightOptions,
    ReliableLightRuntime,
    configured_sources,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def async_setup_entry(
    hass: HomeAssistant, entry: ReliableLightConfigEntry
) -> bool:
    """Set up ReliableLight from a config entry."""
    options = ReliableLightOptions.from_entry(entry)
    if options.persistent_retry and not (
        MIN_PERSISTENT_EXPIRY <= options.command_expiry <= MAX_COMMAND_EXPIRY
    ):
        message = (
            "Persistent retry requires a finite command expiry between "
            f"{MIN_PERSISTENT_EXPIRY} and {MAX_COMMAND_EXPIRY} seconds"
        )
        raise ConfigEntryError(message)
    entry.runtime_data = ReliableLightRuntime(
        options=options,
        source_registry_ids=configured_sources(entry),
        entities={},
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ReliableLightConfigEntry
) -> bool:
    """Unload a ReliableLight config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: ReliableLightConfigEntry
) -> None:
    """Remove persisted data for a config entry."""
    for source_id in configured_sources(entry):
        await Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.{source_id}",
        ).async_remove()
