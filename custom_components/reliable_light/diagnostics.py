"""Diagnostics for ReliableLight."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_POWER, CONF_SOURCE, CONF_SOURCES

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .model import ReliableLightConfigEntry

TO_REDACT = {
    CONF_SOURCES,
    CONF_SOURCE,
    CONF_POWER,
    "source_entity_id",
    "source_registry_id",
    "power_entity_id",
    "power_registry_id",
}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: ReliableLightConfigEntry
) -> dict[str, Any]:
    """Return redacted ReliableLight diagnostics."""
    runtime = entry.runtime_data
    entities = [
        {
            "source_registry_id": source_id,
            "state": entity.extra_state_attributes,
        }
        for source_id, entity in runtime.entities.items()
    ]
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
                "subentries": [
                    {
                        "type": subentry.subentry_type,
                        "title": subentry.title,
                        "data": dict(subentry.data),
                    }
                    for subentry in entry.subentries.values()
                ],
            },
            "entities": entities,
        },
        TO_REDACT,
    )
