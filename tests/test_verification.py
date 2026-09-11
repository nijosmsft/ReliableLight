"""Tests for source state verification."""

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_TRANSITION,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    ColorMode,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import State

from custom_components.reliable_light.model import TOLERANCE_PROFILES
from custom_components.reliable_light.verification import command_matches


def test_on_off_and_unavailable() -> None:
    """Verify basic state handling."""
    tolerance = TOLERANCE_PROFILES["normal"]
    assert command_matches(State("light.test", STATE_OFF), "turn_off", {}, tolerance)
    assert command_matches(State("light.test", STATE_ON), "turn_on", {}, tolerance)
    assert not command_matches(
        State("light.test", STATE_UNAVAILABLE), "turn_on", {}, tolerance
    )


def test_only_explicit_attributes_are_verified() -> None:
    """Ignore unrequested attributes and transition."""
    state = State(
        "light.test",
        STATE_ON,
        {ATTR_BRIGHTNESS: 102, ATTR_HS_COLOR: (358, 50)},
    )
    tolerance = TOLERANCE_PROFILES["normal"]
    assert command_matches(
        state,
        "turn_on",
        {ATTR_BRIGHTNESS: 100, ATTR_TRANSITION: 10},
        tolerance,
    )
    assert command_matches(
        state,
        "turn_on",
        {ATTR_HS_COLOR: (1, 52)},
        tolerance,
    )
    assert not command_matches(
        state,
        "turn_on",
        {ATTR_BRIGHTNESS: 90},
        tolerance,
    )


def test_white_requires_mode_and_brightness() -> None:
    """Verify white commands using mode and brightness."""
    tolerance = TOLERANCE_PROFILES["normal"]
    state = State(
        "light.test",
        STATE_ON,
        {ATTR_COLOR_MODE: ColorMode.WHITE, ATTR_BRIGHTNESS: 200},
    )
    assert command_matches(state, "turn_on", {ATTR_WHITE: 198}, tolerance)
    assert not command_matches(
        State(
            "light.test",
            STATE_ON,
            {ATTR_COLOR_MODE: ColorMode.HS, ATTR_BRIGHTNESS: 200},
        ),
        "turn_on",
        {ATTR_WHITE: 200},
        tolerance,
    )


def test_all_color_and_effect_attributes() -> None:
    """Verify every supported steady-state command attribute."""
    tolerance = TOLERANCE_PROFILES["normal"]
    attributes = {
        ATTR_COLOR_TEMP_KELVIN: 3000,
        ATTR_RGB_COLOR: (10, 20, 30),
        ATTR_RGBW_COLOR: (10, 20, 30, 40),
        ATTR_RGBWW_COLOR: (10, 20, 30, 40, 50),
        ATTR_XY_COLOR: (0.3, 0.4),
        ATTR_EFFECT: "calm",
    }
    state = State("light.test", STATE_ON, attributes)
    assert command_matches(state, "turn_on", {ATTR_COLOR_TEMP_KELVIN: 3050}, tolerance)
    assert command_matches(state, "turn_on", {ATTR_RGB_COLOR: (12, 18, 34)}, tolerance)
    assert command_matches(
        state, "turn_on", {ATTR_RGBW_COLOR: (12, 18, 34, 39)}, tolerance
    )
    assert command_matches(
        state, "turn_on", {ATTR_RGBWW_COLOR: (12, 18, 34, 39, 52)}, tolerance
    )
    assert command_matches(state, "turn_on", {ATTR_XY_COLOR: (0.305, 0.395)}, tolerance)
    assert command_matches(state, "turn_on", {ATTR_EFFECT: "calm"}, tolerance)
    assert not command_matches(state, "turn_on", {"unsupported": True}, tolerance)
    assert not command_matches(
        State("light.test", STATE_ON), "turn_on", {ATTR_EFFECT: "calm"}, tolerance
    )
