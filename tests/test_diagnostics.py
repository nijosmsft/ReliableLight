"""Tests for ReliableLight diagnostics."""

from typing import TYPE_CHECKING

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reliable_light.const import CONF_SOURCES, DOMAIN
from custom_components.reliable_light.diagnostics import (
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er


async def test_diagnostics_redact_sources(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Do not disclose source identifiers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["data"][CONF_SOURCES] == "**REDACTED**"
    assert result["entities"][0]["source_registry_id"] == "**REDACTED**"
