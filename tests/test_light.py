"""Integration tests for ReliableLight proxy entities."""

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityStateAttribute,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reliable_light.const import (
    CONF_POWER,
    CONF_RETRY_INITIAL,
    CONF_RETRY_MAX,
    CONF_SOURCE,
    CONF_SOURCES,
    CONF_VERIFICATION_DELAY,
    DOMAIN,
    SUBENTRY_TYPE_MANAGED_LIGHT,
)


def _register_named_device_source(
    hass: HomeAssistant,
) -> tuple[er.RegistryEntry, dr.DeviceEntry]:
    """Register a source associated with a same-named area and device."""
    source_config = MockConfigEntry(domain="test", title="Test source")
    source_config.add_to_hass(hass)
    area = ar.async_get(hass).async_get_or_create("Kid Room")
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=source_config.entry_id,
        identifiers={("test", "kid-room-light")},
        name="Kid Room",
    )
    device_registry.async_update_device(device.id, area_id=area.id)
    source = er.async_get(hass).async_get_or_create(
        LIGHT_DOMAIN,
        "test",
        "kid-room-ceiling-light",
        config_entry=source_config,
        device_id=device.id,
        has_entity_name=True,
        original_name="Ceiling Light",
        suggested_object_id="ceiling_light",
        capabilities={ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS]},
    )
    hass.states.async_set(
        source.entity_id,
        "off",
        {
            ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS],
            ATTR_COLOR_MODE: ColorMode.BRIGHTNESS,
            EntityStateAttribute.FRIENDLY_NAME: "Kid Room Ceiling Light",
        },
    )
    return source, device


async def test_state_mirroring_and_callable_during_outage(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Mirror source state without optimistic pending state."""

    @callback
    def turn_on(call: ServiceCall) -> None:
        hass.states.async_set(
            source_light.entity_id,
            STATE_ON,
            {
                ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS],
                ATTR_COLOR_MODE: ColorMode.BRIGHTNESS,
                ATTR_BRIGHTNESS: call.data.get(ATTR_BRIGHTNESS, 100),
            },
        )

    hass.services.async_register(LIGHT_DOMAIN, SERVICE_TURN_ON, turn_on)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={
            CONF_RETRY_INITIAL: 0.01,
            CONF_RETRY_MAX: 0.02,
            CONF_VERIFICATION_DELAY: 0,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    proxy_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, source_light.id
    )
    assert proxy_id is not None
    hass.states.async_set(source_light.entity_id, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    proxy = hass.states.get(proxy_id)
    assert proxy is not None
    assert proxy.state == "unknown"
    assert proxy.attributes["source_available"] is False
    assert proxy.attributes[ATTR_BRIGHTNESS] is None

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: proxy_id, ATTR_BRIGHTNESS: 120},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(proxy_id).state == "unknown"
    assert hass.states.get(proxy_id).attributes["pending"] is True

    hass.states.async_set(
        source_light.entity_id,
        "off",
        {
            ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS],
            ATTR_COLOR_MODE: ColorMode.BRIGHTNESS,
            ATTR_BRIGHTNESS: 100,
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get(proxy_id).state == "off"
    assert hass.states.get(proxy_id).attributes["pending"] is True


async def test_unload_removes_entity_and_tasks(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """Unload the platform cleanly."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source_light.id]},
        options={},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    proxy_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, source_light.id
    )
    assert proxy_id is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(proxy_id).state == STATE_UNAVAILABLE


async def test_named_device_source_uses_exact_proxy_object_id(
    hass: HomeAssistant,
) -> None:
    """Do not prefix a new proxy entity ID with its area or device name."""
    source, device = _register_named_device_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source.id]},
        options={},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    proxy_id = er.async_get(hass).async_get_entity_id(LIGHT_DOMAIN, DOMAIN, source.id)
    assert proxy_id == "light.ceiling_light_reliable"
    proxy_entry = er.async_get(hass).async_get(proxy_id)
    assert proxy_entry is not None
    assert proxy_entry.device_id == device.id
    proxy_state = hass.states.get(proxy_id)
    assert proxy_state is not None
    assert (
        proxy_state.attributes[EntityStateAttribute.FRIENDLY_NAME]
        == "Kid Room Ceiling Light Reliable"
    )


async def test_existing_proxy_id_survives_source_rename_and_reload(
    hass: HomeAssistant,
) -> None:
    """Keep the registered proxy ID stable across source changes and reload."""
    source, _device = _register_named_device_source(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ReliableLight",
        data={CONF_SOURCES: [source.id]},
        options={},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    generated_id = registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, source.id)
    assert generated_id == "light.ceiling_light_reliable"
    existing_id = "light.existing_manually_named_reliable"
    registry.async_update_entity(generated_id, new_entity_id=existing_id)
    registry.async_update_entity(
        source.entity_id,
        new_entity_id="light.renamed_ceiling_light",
    )

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get_entity_id(LIGHT_DOMAIN, DOMAIN, source.id) == existing_id
    proxy_entry = registry.async_get(existing_id)
    assert proxy_entry is not None
    assert proxy_entry.unique_id == source.id
    proxy_state = hass.states.get(existing_id)
    assert proxy_state is not None
    assert proxy_state.attributes["source_entity_id"] == "light.renamed_ceiling_light"


async def test_compound_proxy_state_truth_table(
    hass: HomeAssistant,
    source_light: er.RegistryEntry,
    power_switch: er.RegistryEntry,
) -> None:
    """Power state controls compound proxy observability and off semantics."""
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
    proxy_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, source_light.id
    )
    assert proxy_id is not None

    hass.states.async_set(
        source_light.entity_id,
        STATE_ON,
        {
            ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS],
            ATTR_COLOR_MODE: ColorMode.BRIGHTNESS,
            ATTR_BRIGHTNESS: 177,
        },
    )
    hass.states.async_set(power_switch.entity_id, STATE_OFF)
    await hass.async_block_till_done()
    proxy = hass.states.get(proxy_id)
    assert proxy is not None
    assert proxy.state == STATE_OFF
    assert proxy.attributes[ATTR_BRIGHTNESS] is None
    assert proxy.attributes["power_available"] is True

    hass.states.async_set(power_switch.entity_id, STATE_ON)
    await hass.async_block_till_done()
    proxy = hass.states.get(proxy_id)
    assert proxy is not None
    assert proxy.state == STATE_ON
    assert proxy.attributes[ATTR_BRIGHTNESS] == 177

    hass.states.async_set(source_light.entity_id, STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    assert hass.states.get(proxy_id).state == STATE_UNKNOWN

    hass.states.async_set(power_switch.entity_id, STATE_UNAVAILABLE)
    hass.states.async_set(source_light.entity_id, STATE_OFF)
    await hass.async_block_till_done()
    proxy = hass.states.get(proxy_id)
    assert proxy is not None
    assert proxy.state == STATE_UNKNOWN
    assert proxy.attributes["power_available"] is False


async def test_deleting_last_subentry_does_not_restore_shadow_source(
    hass: HomeAssistant, source_light: er.RegistryEntry
) -> None:
    """The compatibility shadow cannot recreate a deleted managed light."""
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
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    proxy_id = er.async_get(hass).async_get_entity_id(
        LIGHT_DOMAIN, DOMAIN, source_light.id
    )
    assert proxy_id is not None

    assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()

    assert not entry.subentries
    assert entry.data[CONF_SOURCES] == []
    assert (
        er.async_get(hass).async_get_entity_id(LIGHT_DOMAIN, DOMAIN, source_light.id)
        is None
    )
