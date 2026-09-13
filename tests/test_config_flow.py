"""Tests for ReliableLight configuration flows."""

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentry
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reliable_light import async_migrate_entry
from custom_components.reliable_light.const import (
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
    DOMAIN,
    SUBENTRY_TYPE_MANAGED_LIGHT,
)
from custom_components.reliable_light.model import ReliableLightOptions

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


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
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["data"][CONF_SOURCES] == [source_light.id]
    assert len(result["result"].subentries) == 1
    subentry = next(iter(result["result"].subentries.values()))
    assert subentry.data[CONF_SOURCE] == source_light.id
    assert subentry.unique_id == source_light.id


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
            CONF_RETRY_INITIAL: 1,
            CONF_RETRY_MAX: 10,
            CONF_POWER_RECOVERY_DELAY: 2,
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


async def test_global_options_do_not_modify_managed_sources(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Editing retry settings cannot delete or replace managed lights."""
    subentry = ConfigSubentry(
        data={CONF_SOURCE: source_light.id},
        subentry_type=SUBENTRY_TYPE_MANAGED_LIGHT,
        title="Source",
        unique_id=source_light.id,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={},
        version=2,
        subentries_data=[subentry.as_dict()],
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_RETRY_INITIAL: 1,
            CONF_RETRY_MAX: 10,
            CONF_POWER_RECOVERY_DELAY: 2,
            CONF_VERIFICATION_DELAY: 0,
            CONF_VERIFICATION_TOLERANCE: "normal",
            CONF_COMMAND_EXPIRY: 0,
            CONF_PERSISTENT_RETRY: False,
            CONF_DIAGNOSTIC_ATTRIBUTES: True,
            CONF_EMIT_EVENTS: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_POWER_RECOVERY_DELAY] == 2
    assert entry.data[CONF_SOURCES] == [source_light.id]
    assert entry.subentries[subentry.subentry_id].data == {CONF_SOURCE: source_light.id}


async def test_legacy_migration_preserves_proxy_identity_and_custom_name(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Migrate v0.1.3 sources without replacing their registered proxies."""
    registry = er.async_get(hass)
    proxy = registry.async_get_or_create(
        "light",
        DOMAIN,
        source_light.id,
        suggested_object_id="source_reliable",
        original_name="Source Reliable",
    )
    registry.async_update_entity(
        proxy.entity_id,
        new_entity_id="light.my_custom_reliable",
        name="Custom reliable name",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={CONF_SOURCES: [source_light.id]},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert CONF_SOURCES not in entry.options
    assert entry.data[CONF_SOURCES] == [source_light.id]
    assert len(entry.subentries) == 1
    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_TYPE_MANAGED_LIGHT
    assert subentry.data == {CONF_SOURCE: source_light.id}

    migrated_proxy = registry.async_get("light.my_custom_reliable")
    assert migrated_proxy is not None
    assert migrated_proxy.unique_id == source_light.id
    assert migrated_proxy.name == "Custom reliable name"
    assert migrated_proxy.config_subentry_id == subentry.subentry_id

    assert await async_migrate_entry(hass, entry)
    assert len(entry.subentries) == 1


async def test_v020_migration_preserves_subentries_entities_and_options(
    hass: HomeAssistant,
    source_light: er.RegistryEntry,
    power_switch: er.RegistryEntry,
) -> None:
    """The behavior-fix migration changes only the config minor version."""
    subentry = ConfigSubentry(
        data={
            CONF_SOURCE: source_light.id,
            CONF_POWER: power_switch.id,
        },
        subentry_type=SUBENTRY_TYPE_MANAGED_LIGHT,
        title="Source",
        unique_id=source_light.id,
    )
    original_options = {
        CONF_RETRY_INITIAL: 3,
        CONF_RETRY_MAX: 30,
        CONF_VERIFICATION_DELAY: 1,
        CONF_VERIFICATION_TOLERANCE: "normal",
        CONF_COMMAND_EXPIRY: 300,
        CONF_PERSISTENT_RETRY: True,
        CONF_DIAGNOSTIC_ATTRIBUTES: True,
        CONF_EMIT_EVENTS: False,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options=original_options,
        version=2,
        minor_version=1,
        subentries_data=[subentry.as_dict()],
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    proxy = registry.async_get_or_create(
        "light",
        DOMAIN,
        source_light.id,
        config_entry=entry,
        config_subentry_id=subentry.subentry_id,
        suggested_object_id="source_reliable",
    )
    registry.async_update_entity(
        proxy.entity_id,
        new_entity_id="light.preserved_reliable",
    )

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert entry.minor_version == 2
    assert dict(entry.options) == original_options
    assert ReliableLightOptions.from_entry(entry).power_recovery_delay == 2
    assert entry.data == {CONF_SOURCES: [source_light.id]}
    assert entry.subentries[subentry.subentry_id].data == {
        CONF_SOURCE: source_light.id,
        CONF_POWER: power_switch.id,
    }
    assert (
        registry.async_get_entity_id("light", DOMAIN, source_light.id)
        == "light.preserved_reliable"
    )


async def test_subentry_add_reconfigure_and_delete(
    hass: HomeAssistant,
    source_light: er.RegistryEntry,
    power_switch: er.RegistryEntry,
) -> None:
    """Manage a source and immutable identity through public subentry APIs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: []},
        options={},
        version=2,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MANAGED_LIGHT),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE: source_light.entity_id,
            CONF_POWER: power_switch.entity_id,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    assert subentry.data == {
        CONF_SOURCE: source_light.id,
        CONF_POWER: power_switch.id,
    }

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry.subentry_id].data == {CONF_SOURCE: source_light.id}

    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    assert not entry.subentries


async def test_subentry_rejects_duplicate_power_owner_and_configured_source(
    hass: HomeAssistant,
    source_light: er.RegistryEntry,
    second_source_light: er.RegistryEntry,
    power_switch: er.RegistryEntry,
) -> None:
    """One dependency cannot power two managed sources or be another source."""
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

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MANAGED_LIGHT),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE: second_source_light.entity_id,
            CONF_POWER: power_switch.entity_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "duplicate_power"


async def test_subentry_rejects_source_as_power_and_reliable_proxy(
    hass: HomeAssistant,
    source_light: er.RegistryEntry,
    reliable_proxy_entry: er.RegistryEntry,
) -> None:
    """Reject invalid upstream power identities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: []},
        options={},
        version=2,
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MANAGED_LIGHT),
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE: source_light.entity_id,
            CONF_POWER: source_light.entity_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "power_is_source"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_SOURCE: source_light.entity_id,
            CONF_POWER: reliable_proxy_entry.entity_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "reliable_light_power"
