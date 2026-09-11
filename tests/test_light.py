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
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.reliable_light.const import (
    CONF_RETRY_INITIAL,
    CONF_RETRY_MAX,
    CONF_SOURCES,
    CONF_VERIFICATION_DELAY,
    DOMAIN,
)


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
