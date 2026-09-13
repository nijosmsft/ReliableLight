"""The ReliableLight integration."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    CONF_SOURCE,
    CONF_SOURCES,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MAX_COMMAND_EXPIRY,
    MIN_PERSISTENT_EXPIRY,
    PLATFORMS,
    STORAGE_VERSION,
    SUBENTRY_TYPE_MANAGED_LIGHT,
)
from .model import (
    ReliableLightConfigEntry,
    ReliableLightOptions,
    ReliableLightRuntime,
    configured_managed_lights,
    configured_sources,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _source_title(hass: HomeAssistant, source_id: str) -> str:
    """Return a readable subentry title for a source registry UUID."""
    registry = er.async_get(hass)
    source = registry.async_get(source_id)
    if source is None:
        return "Managed light"
    return er.async_get_unprefixed_name(hass, source) or source.entity_id


def _add_missing_subentries(
    hass: HomeAssistant,
    entry: ReliableLightConfigEntry,
    source_ids: tuple[str, ...],
) -> None:
    """Create managed-light subentries without duplicating existing sources."""
    existing = {
        str(subentry.data.get(CONF_SOURCE))
        for subentry in entry.get_subentries_of_type(SUBENTRY_TYPE_MANAGED_LIGHT)
    }
    registry = er.async_get(hass)
    for source_id in source_ids:
        if source_id in existing:
            continue
        subentry = ConfigSubentry(
            data=MappingProxyType({CONF_SOURCE: source_id}),
            subentry_type=SUBENTRY_TYPE_MANAGED_LIGHT,
            title=_source_title(hass, source_id),
            unique_id=source_id,
        )
        hass.config_entries.async_add_subentry(entry, subentry)
        if proxy_id := registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, source_id):
            registry.async_update_entity(
                proxy_id, config_subentry_id=subentry.subentry_id
            )
        existing.add(source_id)


async def async_migrate_entry(
    hass: HomeAssistant, entry: ReliableLightConfigEntry
) -> bool:
    """Migrate legacy source lists to managed-light config subentries."""
    if (
        entry.version == CONFIG_ENTRY_VERSION
        and entry.minor_version >= CONFIG_ENTRY_MINOR_VERSION
    ):
        return True

    if entry.version < CONFIG_ENTRY_VERSION:
        source_ids = configured_sources(entry)
        _add_missing_subentries(hass, entry, source_ids)
        options = dict(entry.options)
        options.pop(CONF_SOURCES, None)
        data = {**entry.data, CONF_SOURCES: list(source_ids)}
    else:
        options = dict(entry.options)
        data = dict(entry.data)
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=CONFIG_ENTRY_VERSION,
        minor_version=CONFIG_ENTRY_MINOR_VERSION,
    )
    return True


async def _async_reload_entry(
    hass: HomeAssistant, entry: ReliableLightConfigEntry
) -> None:
    """Reload after a parent option or managed-light subentry change."""
    await hass.config_entries.async_reload(entry.entry_id)


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
    if not configured_managed_lights(entry):
        _add_missing_subentries(hass, entry, configured_sources(entry))

    managed_lights = configured_managed_lights(entry)
    source_ids = tuple(item.source_registry_id for item in managed_lights)
    shadow_sources = tuple(str(value) for value in entry.data.get(CONF_SOURCES, ()))
    for removed_source in set(shadow_sources) - set(source_ids):
        await Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.{removed_source}",
        ).async_remove()
    if shadow_sources != source_ids or CONF_SOURCES in entry.options:
        options_data = dict(entry.options)
        options_data.pop(CONF_SOURCES, None)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_SOURCES: list(source_ids)},
            options=options_data,
        )

    entry.runtime_data = ReliableLightRuntime(
        options=options,
        managed_lights=managed_lights,
        entities={},
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
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
    source_ids = set(configured_sources(entry))
    source_ids.update(str(value) for value in entry.data.get(CONF_SOURCES, ()))
    for source_id in source_ids:
        await Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.{source_id}",
        ).async_remove()
