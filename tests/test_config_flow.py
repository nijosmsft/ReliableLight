"""Tests for ReliableLight configuration flows."""

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reliable_light.const import (
    CONF_COMMAND_EXPIRY,
    CONF_DIAGNOSTIC_ATTRIBUTES,
    CONF_EMIT_EVENTS,
    CONF_PERSISTENT_RETRY,
    CONF_RETRY_INITIAL,
    CONF_RETRY_MAX,
    CONF_SOURCES,
    CONF_VERIFICATION_DELAY,
    CONF_VERIFICATION_TOLERANCE,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity_registry as er


async def test_config_flow_stores_registry_uuid(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Store stable source identity rather than entity id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCES: [source_light.entity_id]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SOURCES] == [source_light.id]


async def test_rejects_reliable_proxy(
    hass: HomeAssistant, reliable_proxy_entry: er.RegistryEntry
) -> None:
    """Reject ReliableLight entities as sources."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SOURCES: [reliable_proxy_entry.entity_id]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "reliable_light_source"


async def test_second_config_flow_aborts_with_single_instance(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Keep one config entry and direct users to its options flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_require_persistent_expiry(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Require finite expiry for persistent retry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SOURCES: [source_light.entity_id],
            CONF_RETRY_INITIAL: 1,
            CONF_RETRY_MAX: 10,
            CONF_VERIFICATION_DELAY: 0,
            CONF_VERIFICATION_TOLERANCE: "normal",
            CONF_COMMAND_EXPIRY: 0,
            CONF_PERSISTENT_RETRY: True,
            CONF_DIAGNOSTIC_ATTRIBUTES: True,
            CONF_EMIT_EVENTS: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "persistent_expiry_required"
