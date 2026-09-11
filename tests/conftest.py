"""Shared fixtures for ReliableLight tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_SUPPORTED_COLOR_MODES,
    ColorMode,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import STATE_OFF
from homeassistant.helpers import entity_registry as er

from custom_components.reliable_light.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> None:
    """Enable loading custom integrations."""


@pytest.fixture
def source_light(hass: HomeAssistant) -> er.RegistryEntry:
    """Register a synthetic source light."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        LIGHT_DOMAIN,
        "test",
        "source-1",
        suggested_object_id="source",
        original_name="Source",
        supported_features=0,
        capabilities={ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS]},
    )
    hass.states.async_set(
        entry.entity_id,
        STATE_OFF,
        {
            ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS],
            ATTR_COLOR_MODE: ColorMode.BRIGHTNESS,
            ATTR_BRIGHTNESS: 100,
        },
    )
    return entry


@pytest.fixture
def second_source_light(hass: HomeAssistant) -> er.RegistryEntry:
    """Register a second synthetic source light."""
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        LIGHT_DOMAIN,
        "test",
        "source-2",
        suggested_object_id="source_two",
        original_name="Source Two",
        supported_features=0,
        capabilities={ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS]},
    )
    hass.states.async_set(
        entry.entity_id,
        STATE_OFF,
        {
            ATTR_SUPPORTED_COLOR_MODES: [ColorMode.BRIGHTNESS],
            ATTR_COLOR_MODE: ColorMode.BRIGHTNESS,
            ATTR_BRIGHTNESS: 100,
        },
    )
    return entry


@pytest.fixture
def reliable_proxy_entry(hass: HomeAssistant) -> er.RegistryEntry:
    """Register a synthetic ReliableLight proxy."""
    return er.async_get(hass).async_get_or_create(
        LIGHT_DOMAIN,
        DOMAIN,
        "proxy-source",
        suggested_object_id="proxy",
        original_name="Proxy",
        capabilities={ATTR_SUPPORTED_COLOR_MODES: [ColorMode.ONOFF]},
    )
