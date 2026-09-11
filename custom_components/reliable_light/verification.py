"""Verify source light state against desired commands."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

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
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

if TYPE_CHECKING:
    from homeassistant.core import State

    from .model import VerificationTolerance


def _close(actual: Any, desired: Any, tolerance: float) -> bool:
    return (
        isinstance(actual, (int, float))
        and isinstance(desired, (int, float))
        and abs(float(actual) - float(desired)) <= tolerance
    )


def _sequence_close(
    actual: Any, desired: Any, tolerance: float, expected_length: int
) -> bool:
    return (
        isinstance(actual, Sequence)
        and not isinstance(actual, str)
        and isinstance(desired, Sequence)
        and not isinstance(desired, str)
        and len(actual) == expected_length
        and len(desired) == expected_length
        and all(
            _close(left, right, tolerance)
            for left, right in zip(actual, desired, strict=False)
        )
    )


def _hs_close(actual: Any, desired: Any, tolerance: VerificationTolerance) -> bool:
    if not _sequence_close(actual, desired, float("inf"), 2):
        return False
    hue_distance = abs(float(actual[0]) - float(desired[0])) % 360
    hue_distance = min(hue_distance, 360 - hue_distance)
    return (
        hue_distance <= tolerance.hue
        and abs(float(actual[1]) - float(desired[1])) <= tolerance.saturation
    )


def command_matches(
    state: State | None,
    action: str,
    kwargs: dict[str, Any],
    tolerance: VerificationTolerance,
) -> bool:
    """Return whether a source state satisfies a desired command."""
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return False

    if action == "turn_off":
        return state.state == STATE_OFF

    if action != "turn_on" or state.state != STATE_ON:
        return False

    attributes = state.attributes
    for key, desired in kwargs.items():
        if key == ATTR_TRANSITION:
            continue
        if key == ATTR_BRIGHTNESS:
            if not _close(
                attributes.get(ATTR_BRIGHTNESS), desired, tolerance.brightness
            ):
                return False
        elif key == ATTR_COLOR_TEMP_KELVIN:
            if not _close(
                attributes.get(ATTR_COLOR_TEMP_KELVIN),
                desired,
                tolerance.color_temp_kelvin,
            ):
                return False
        elif key == ATTR_HS_COLOR:
            if not _hs_close(attributes.get(ATTR_HS_COLOR), desired, tolerance):
                return False
        elif key == ATTR_RGB_COLOR:
            if not _sequence_close(
                attributes.get(ATTR_RGB_COLOR),
                desired,
                tolerance.color_channel,
                3,
            ):
                return False
        elif key == ATTR_RGBW_COLOR:
            if not _sequence_close(
                attributes.get(ATTR_RGBW_COLOR),
                desired,
                tolerance.color_channel,
                4,
            ):
                return False
        elif key == ATTR_RGBWW_COLOR:
            if not _sequence_close(
                attributes.get(ATTR_RGBWW_COLOR),
                desired,
                tolerance.color_channel,
                5,
            ):
                return False
        elif key == ATTR_XY_COLOR:
            if not _sequence_close(
                attributes.get(ATTR_XY_COLOR), desired, tolerance.xy, 2
            ):
                return False
        elif key == ATTR_WHITE:
            if attributes.get(ATTR_COLOR_MODE) != ColorMode.WHITE or not _close(
                attributes.get(ATTR_BRIGHTNESS), desired, tolerance.brightness
            ):
                return False
        elif key == ATTR_EFFECT:
            if attributes.get(ATTR_EFFECT) != desired:
                return False
        else:
            return False
    return True
