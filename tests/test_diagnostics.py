"""Tests for ReliableLight diagnostics."""

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reliable_light.const import (
    CONF_POWER,
    CONF_SOURCE,
    CONF_SOURCES,
    DOMAIN,
    SUBENTRY_TYPE_MANAGED_LIGHT,
)
from custom_components.reliable_light.diagnostics import (
    async_get_config_entry_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er


async def test_diagnostics_redact_sources(
    hass: HomeAssistant,
    source_light: er.RegistryEntry,
    power_switch: er.RegistryEntry,
) -> None:
    """Do not disclose source or power identifiers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={},
        version=2,
        subentries_data=[
            ConfigSubentry(
                data={
                    CONF_SOURCE: source_light.id,
                    CONF_POWER: power_switch.id,
                },
                subentry_type=SUBENTRY_TYPE_MANAGED_LIGHT,
                title="Source",
                unique_id=source_light.id,
            ).as_dict()
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["data"][CONF_SOURCES] == "**REDACTED**"
    assert result["entities"][0]["source_registry_id"] == "**REDACTED**"
    assert result["entities"][0]["state"]["power_entity_id"] == "**REDACTED**"
