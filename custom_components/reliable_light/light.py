"""ReliableLight proxy light entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    valid_supported_color_modes,
)
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    ATTR_ATTEMPT_COUNT,
    ATTR_LAST_KNOWN_STATE,
    ATTR_LAST_RESULT,
    ATTR_LAST_VERIFIED_AT,
    ATTR_NEXT_RETRY_AT,
    ATTR_PENDING,
    ATTR_PENDING_ACTION,
    ATTR_PENDING_SINCE,
    ATTR_SOURCE_AVAILABLE,
    ATTR_SOURCE_ENTITY_ID,
    ATTR_SOURCE_STATE,
    ATTR_WORKER_FAILED,
)
from .worker import ReliableLightWorker

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .model import ReliableLightConfigEntry

SUPPORTED_FEATURE_MASK = LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
VALID_SOURCE_STATES = (STATE_ON, STATE_OFF)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReliableLightConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ReliableLight entities."""
    active_contexts: set[str] = set()
    entities = [
        ReliableLightEntity(hass, entry, source_id, active_contexts)
        for source_id in entry.runtime_data.source_registry_ids
    ]
    entry.runtime_data.entities = {
        entity.source_registry_id: entity for entity in entities
    }
    async_add_entities(entities)


class ReliableLightEntity(LightEntity, RestoreEntity):
    """A reliable command proxy around another light entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ReliableLightConfigEntry,
        source_registry_id: str,
        active_contexts: set[str],
    ) -> None:
        """Initialize a reliable proxy light."""
        self._hass_ref = hass
        self._entry = entry
        self._source_registry_id = source_registry_id
        self._source_entity_id: str | None = None
        self._source_state = "missing"
        self._last_known_state: str | None = None
        self._state_unsub: Callable[[], None] | None = None
        self._attr_unique_id = source_registry_id
        self._attr_name = "Reliable"
        self._attr_assumed_state = True
        self._attr_is_on = None
        self._worker = ReliableLightWorker(
            hass,
            entry.entry_id,
            source_registry_id,
            entry.runtime_data.options,
            lambda: self._source_entity_id,
            self._async_worker_changed,
            active_contexts,
        )
        self._resolve_source()

    @property
    def source_registry_id(self) -> str:
        """Return the stable source registry UUID."""
        return self._source_registry_id

    @property
    def available(self) -> bool:
        """Keep the command endpoint callable during source outages."""
        return not self._worker.failed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return source and pending diagnostics."""
        attributes: dict[str, Any] = {
            ATTR_SOURCE_ENTITY_ID: self._source_entity_id,
            ATTR_SOURCE_STATE: self._source_state,
            ATTR_SOURCE_AVAILABLE: self._source_state
            not in ("missing", STATE_UNKNOWN, STATE_UNAVAILABLE),
            ATTR_LAST_KNOWN_STATE: self._last_known_state,
        }
        if self._entry.runtime_data.options.diagnostic_attributes:
            diagnostics = self._worker.diagnostics()
            attributes.update(
                {
                    ATTR_PENDING: diagnostics["pending"],
                    ATTR_PENDING_ACTION: diagnostics["pending_action"],
                    ATTR_PENDING_SINCE: diagnostics["pending_since"],
                    ATTR_ATTEMPT_COUNT: diagnostics["attempt_count"],
                    ATTR_NEXT_RETRY_AT: diagnostics["next_retry_at"],
                    ATTR_LAST_RESULT: diagnostics["last_result"],
                    ATTR_LAST_VERIFIED_AT: diagnostics["last_verified_at"],
                    ATTR_WORKER_FAILED: diagnostics["worker_failed"],
                }
            )
        return attributes

    async def async_added_to_hass(self) -> None:
        """Subscribe to source changes and start the worker."""
        await super().async_added_to_hass()
        if (
            self._source_state not in VALID_SOURCE_STATES
            and (last_state := await self.async_get_last_state()) is not None
        ):
            self._update_capabilities(last_state)
            restored_last_known = last_state.attributes.get(ATTR_LAST_KNOWN_STATE)
            if restored_last_known in VALID_SOURCE_STATES:
                self._last_known_state = restored_last_known
        self._subscribe_source()
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._async_registry_updated,
            )
        )
        await self._worker.async_initialize()
        self._worker.start()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop worker activity cleanly."""
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        await self._worker.async_shutdown()
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Submit a desired turn-on command."""
        if self._worker.is_internal_context(self._context):
            msg = "ReliableLight source cycle detected"
            raise ServiceValidationError(msg)
        await self._worker.async_submit(SERVICE_TURN_ON, kwargs, self._context)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Submit a desired turn-off command."""
        if self._worker.is_internal_context(self._context):
            msg = "ReliableLight source cycle detected"
            raise ServiceValidationError(msg)
        await self._worker.async_submit(SERVICE_TURN_OFF, kwargs, self._context)

    @callback
    def _async_worker_changed(self) -> None:
        if self.hass is not None and self.entity_id is not None:
            self.async_write_ha_state()

    @callback
    def _async_source_changed(self, event: Event) -> None:
        self._apply_source_state(event.data["new_state"])
        self._worker.notify_source_change()
        self.async_write_ha_state()

    @callback
    def _async_registry_updated(self, event: Event) -> None:
        data = event.data
        current = self._source_entity_id
        if data.get("action") == "create" and current is None:
            source_entry = er.async_get(self.hass).async_get(self._source_registry_id)
            if source_entry is None:
                return
            self._resolve_source()
            self._subscribe_source()
            self._worker.notify_source_change()
            self.async_write_ha_state()
            return
        if current not in (data.get("entity_id"), data.get("old_entity_id")):
            return
        self._resolve_source()
        self._subscribe_source()
        self._worker.notify_source_change()
        self.async_write_ha_state()

    def _resolve_source(self) -> None:
        registry = er.async_get(self._hass_ref)
        source_entry = registry.async_get(self._source_registry_id)
        if source_entry is None:
            self._source_entity_id = None
            self.device_entry = None
            self._apply_source_state(None)
            return
        self._source_entity_id = source_entry.entity_id
        self.device_entry = async_entity_id_to_device(
            self._hass_ref, source_entry.entity_id
        )
        if source_entry.capabilities:
            self._update_capability_attributes(
                {
                    **source_entry.capabilities,
                    "supported_features": source_entry.supported_features,
                }
            )
        object_id = source_entry.entity_id.partition(".")[2]
        self._attr_suggested_object_id = f"{object_id}_reliable"
        if self.device_entry is None:
            source_state = self._hass_ref.states.get(source_entry.entity_id)
            source_name = source_state.name if source_state is not None else object_id
            self._attr_name = f"{source_name} Reliable"
        self._apply_source_state(self._hass_ref.states.get(source_entry.entity_id))

    def _subscribe_source(self) -> None:
        if self._state_unsub is not None:
            self._state_unsub()
            self._state_unsub = None
        if self._source_entity_id is not None:
            self._state_unsub = async_track_state_change_event(
                self.hass,
                [self._source_entity_id],
                self._async_source_changed,
            )

    def _apply_source_state(self, state: State | None) -> None:
        if state is None:
            self._source_state = "missing"
            self._attr_is_on = None
            self._attr_assumed_state = True
            self._clear_actual_attributes()
            return

        self._source_state = state.state
        self._update_capabilities(state)
        if state.state not in VALID_SOURCE_STATES:
            self._attr_is_on = None
            self._attr_assumed_state = True
            self._clear_actual_attributes()
            return

        self._last_known_state = state.state
        self._attr_is_on = state.state == STATE_ON
        self._attr_assumed_state = False
        attributes = state.attributes
        self._attr_brightness = attributes.get(ATTR_BRIGHTNESS)
        color_mode = attributes.get(ATTR_COLOR_MODE)
        try:
            self._attr_color_mode = ColorMode(color_mode) if color_mode else None
        except ValueError:
            self._attr_color_mode = None
        self._attr_color_temp_kelvin = attributes.get(ATTR_COLOR_TEMP_KELVIN)
        self._attr_hs_color = attributes.get(ATTR_HS_COLOR)
        self._attr_rgb_color = attributes.get(ATTR_RGB_COLOR)
        self._attr_rgbw_color = attributes.get(ATTR_RGBW_COLOR)
        self._attr_rgbww_color = attributes.get(ATTR_RGBWW_COLOR)
        self._attr_xy_color = attributes.get(ATTR_XY_COLOR)
        self._attr_effect = attributes.get(ATTR_EFFECT)

    def _clear_actual_attributes(self) -> None:
        """Clear values that are not currently observable from the source."""
        self._attr_brightness = None
        self._attr_color_mode = None
        self._attr_color_temp_kelvin = None
        self._attr_hs_color = None
        self._attr_rgb_color = None
        self._attr_rgbw_color = None
        self._attr_rgbww_color = None
        self._attr_xy_color = None
        self._attr_effect = None

    def _update_capabilities(self, state: State) -> None:
        self._update_capability_attributes(state.attributes)

    def _update_capability_attributes(self, attributes: Mapping[str, Any]) -> None:
        """Update advertised capabilities from state or registry attributes."""
        raw_modes = attributes.get(ATTR_SUPPORTED_COLOR_MODES)
        if raw_modes:
            try:
                modes = valid_supported_color_modes(
                    ColorMode(mode) for mode in raw_modes
                )
            except (ValueError, vol.Invalid):
                modes = None
            if modes:
                self._attr_supported_color_modes = modes

        features = LightEntityFeature(int(attributes.get("supported_features", 0)))
        self._attr_supported_features = features & SUPPORTED_FEATURE_MASK
        self._attr_effect_list = attributes.get(ATTR_EFFECT_LIST)
        self._attr_min_color_temp_kelvin = attributes.get(ATTR_MIN_COLOR_TEMP_KELVIN)
        self._attr_max_color_temp_kelvin = attributes.get(ATTR_MAX_COLOR_TEMP_KELVIN)
