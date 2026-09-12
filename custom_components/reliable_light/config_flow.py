"""Config flow for ReliableLight."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryFlow
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_COMMAND_EXPIRY,
    CONF_DIAGNOSTIC_ATTRIBUTES,
    CONF_EMIT_EVENTS,
    CONF_PERSISTENT_RETRY,
    CONF_POWER,
    CONF_RETRY_INITIAL,
    CONF_RETRY_MAX,
    CONF_SOURCE,
    CONF_SOURCES,
    CONF_VERIFICATION_DELAY,
    CONF_VERIFICATION_TOLERANCE,
    CONFIG_ENTRY_VERSION,
    DEFAULT_COMMAND_EXPIRY,
    DEFAULT_DIAGNOSTIC_ATTRIBUTES,
    DEFAULT_EMIT_EVENTS,
    DEFAULT_PERSISTENT_RETRY,
    DEFAULT_RETRY_INITIAL,
    DEFAULT_RETRY_MAX,
    DEFAULT_VERIFICATION_DELAY,
    DEFAULT_VERIFICATION_TOLERANCE,
    DOMAIN,
    MAX_COMMAND_EXPIRY,
    MIN_PERSISTENT_EXPIRY,
    SUBENTRY_TYPE_MANAGED_LIGHT,
)
from .model import TOLERANCE_PROFILES, configured_managed_lights

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult, SubentryFlowResult
    from homeassistant.core import HomeAssistant


def _group_members(hass: HomeAssistant, entity_id: str) -> list[str]:
    state = hass.states.get(entity_id)
    if state is None:
        return []
    members = state.attributes.get("entity_id")
    if not isinstance(members, (list, tuple)):
        return []
    return [member for member in members if isinstance(member, str)]


def _contains_entity(
    hass: HomeAssistant, entity_id: str, target: str, seen: set[str]
) -> bool:
    if entity_id == target:
        return True
    if entity_id in seen:
        return False
    seen.add(entity_id)
    return any(
        _contains_entity(hass, member, target, seen)
        for member in _group_members(hass, entity_id)
    )


def _contains_reliable_light(
    hass: HomeAssistant, entity_id: str, seen: set[str]
) -> bool:
    if entity_id in seen:
        return True
    seen.add(entity_id)
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is not None and entry.platform == DOMAIN:
        return True
    return any(
        _contains_reliable_light(hass, member, seen.copy())
        for member in _group_members(hass, entity_id)
    )


def _source_title(hass: HomeAssistant, source_id: str) -> str:
    registry = er.async_get(hass)
    source = registry.async_get(source_id)
    if source is None:
        return "Managed light"
    return er.async_get_unprefixed_name(hass, source) or source.entity_id


def _validate_source(
    hass: HomeAssistant,
    entity_id: str,
    *,
    configured_source_ids: set[str],
    configured_power_ids: set[str],
) -> tuple[str | None, str | None]:
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.domain != Platform.LIGHT:
        return None, "source_not_registered"
    if entry.platform == DOMAIN:
        return None, "reliable_light_source"
    if _contains_reliable_light(hass, entity_id, set()):
        return None, "source_cycle"
    if entry.id in configured_source_ids:
        return None, "duplicate_source"
    if entry.id in configured_power_ids:
        return None, "source_is_power_dependency"
    for power_id in configured_power_ids:
        power = registry.async_get(power_id)
        if power is not None and (
            _contains_entity(hass, entity_id, power.entity_id, set())
            or _contains_entity(hass, power.entity_id, entity_id, set())
        ):
            return None, "source_cycle"
    return entry.id, None


def _validate_power(
    hass: HomeAssistant,
    entity_id: str | None,
    *,
    source_id: str,
    configured_source_ids: set[str],
    configured_power_ids: set[str],
) -> tuple[str | None, str | None]:
    if not entity_id:
        return None, None
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None or entry.domain not in (Platform.LIGHT, Platform.SWITCH):
        return None, "power_not_registered"
    if entry.platform == DOMAIN:
        return None, "reliable_light_power"
    if entry.id == source_id:
        return None, "power_is_source"
    if entry.id in configured_source_ids:
        return None, "power_is_configured_source"
    if entry.id in configured_power_ids:
        return None, "duplicate_power"

    source = registry.async_get(source_id)
    if source is not None and (
        _contains_entity(hass, source.entity_id, entity_id, set())
        or _contains_entity(hass, entity_id, source.entity_id, set())
    ):
        return None, "power_cycle"
    for configured_source_id in configured_source_ids:
        configured_source = registry.async_get(configured_source_id)
        if configured_source is not None and _contains_entity(
            hass, entity_id, configured_source.entity_id, set()
        ):
            return None, "power_cycle"
    if _contains_reliable_light(hass, entity_id, set()):
        return None, "power_cycle"
    return entry.id, None


def _validate_managed_light(
    hass: HomeAssistant,
    source_entity_id: str,
    power_entity_id: str | None,
    *,
    entry: config_entries.ConfigEntry | None = None,
    exclude_subentry_id: str | None = None,
) -> tuple[dict[str, str] | None, str | None]:
    managed = configured_managed_lights(entry) if entry is not None else ()
    others = [item for item in managed if item.subentry_id != exclude_subentry_id]
    source_ids = {item.source_registry_id for item in others}
    power_ids = {
        item.power_registry_id for item in others if item.power_registry_id is not None
    }
    source_id, error = _validate_source(
        hass,
        source_entity_id,
        configured_source_ids=source_ids,
        configured_power_ids=power_ids,
    )
    if error is not None:
        return None, error
    assert source_id is not None
    power_id, error = _validate_power(
        hass,
        power_entity_id,
        source_id=source_id,
        configured_source_ids=source_ids | {source_id},
        configured_power_ids=power_ids,
    )
    if error is not None:
        return None, error
    data = {CONF_SOURCE: source_id}
    if power_id is not None:
        data[CONF_POWER] = power_id
    return data, None


def _source_schema(default: list[str] | None = None) -> vol.Schema:
    marker = (
        vol.Required(CONF_SOURCES, default=default)
        if default
        else vol.Required(CONF_SOURCES)
    )
    return vol.Schema(
        {
            marker: selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=Platform.LIGHT,
                    multiple=True,
                )
            )
        }
    )


def _managed_light_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SOURCE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=Platform.LIGHT)
            ),
            vol.Optional(CONF_POWER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=[Platform.LIGHT, Platform.SWITCH])
            ),
        }
    )


def _power_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_POWER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=[Platform.LIGHT, Platform.SWITCH])
            )
        }
    )


class ReliableLightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a ReliableLight config flow."""

    VERSION = CONFIG_ENTRY_VERSION
    MINOR_VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return ReliableLightOptionsFlow()

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported managed-light subentries."""
        return {SUBENTRY_TYPE_MANAGED_LIGHT: ManagedLightSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the singleton ReliableLight entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            source_ids: list[str] = []
            for entity_id in user_input[CONF_SOURCES]:
                source_id, error = _validate_source(
                    self.hass,
                    entity_id,
                    configured_source_ids=set(source_ids),
                    configured_power_ids=set(),
                )
                if error is not None:
                    errors["base"] = error
                    break
                assert source_id is not None
                source_ids.append(source_id)
            if not source_ids and "base" not in errors:
                errors["base"] = "no_sources"
            if not errors:
                return self.async_create_entry(
                    title="ReliableLight",
                    data={CONF_SOURCES: source_ids},
                    subentries=[
                        {
                            "data": {CONF_SOURCE: source_id},
                            "subentry_type": SUBENTRY_TYPE_MANAGED_LIGHT,
                            "title": _source_title(self.hass, source_id),
                            "unique_id": source_id,
                        }
                        for source_id in source_ids
                    ],
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_source_schema(),
            errors=errors,
        )


class ManagedLightSubentryFlow(ConfigSubentryFlow):
    """Add and reconfigure one managed source light."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a managed light."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, error = _validate_managed_light(
                self.hass,
                user_input[CONF_SOURCE],
                user_input.get(CONF_POWER),
                entry=self._get_entry(),
            )
            if error is None:
                assert data is not None
                return self.async_create_entry(
                    title=_source_title(self.hass, data[CONF_SOURCE]),
                    data=data,
                    unique_id=data[CONF_SOURCE],
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=_managed_light_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change only the optional power dependency."""
        subentry = self._get_reconfigure_subentry()
        source_id = str(subentry.data[CONF_SOURCE])
        registry = er.async_get(self.hass)
        power_id = subentry.data.get(CONF_POWER)
        power = registry.async_get(power_id) if power_id else None
        power_entity_id = power.entity_id if power is not None else None
        errors: dict[str, str] = {}
        if user_input is not None:
            others = [
                item
                for item in configured_managed_lights(self._get_entry())
                if item.subentry_id != subentry.subentry_id
            ]
            power_id, error = _validate_power(
                self.hass,
                user_input.get(CONF_POWER),
                source_id=source_id,
                configured_source_ids={item.source_registry_id for item in others}
                | {source_id},
                configured_power_ids={
                    item.power_registry_id
                    for item in others
                    if item.power_registry_id is not None
                },
            )
            if error is None:
                data = {CONF_SOURCE: source_id}
                if power_id is not None:
                    data[CONF_POWER] = power_id
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data=data,
                    title=_source_title(self.hass, source_id),
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _power_schema(),
                {CONF_POWER: power_entity_id},
            ),
            errors=errors,
        )


class ReliableLightOptionsFlow(config_entries.OptionsFlow):
    """Manage global ReliableLight behavior."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update global retry behavior."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if float(user_input[CONF_RETRY_INITIAL]) > float(
                user_input[CONF_RETRY_MAX]
            ):
                errors["base"] = "invalid_backoff"
            elif bool(user_input[CONF_PERSISTENT_RETRY]) and not (
                MIN_PERSISTENT_EXPIRY
                <= int(user_input[CONF_COMMAND_EXPIRY])
                <= MAX_COMMAND_EXPIRY
            ):
                errors["base"] = "persistent_expiry_required"
            else:
                return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RETRY_INITIAL,
                    default=current.get(CONF_RETRY_INITIAL, DEFAULT_RETRY_INITIAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.1,
                        max=300,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_RETRY_MAX,
                    default=current.get(CONF_RETRY_MAX, DEFAULT_RETRY_MAX),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=3600,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_VERIFICATION_DELAY,
                    default=current.get(
                        CONF_VERIFICATION_DELAY, DEFAULT_VERIFICATION_DELAY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=60,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_VERIFICATION_TOLERANCE,
                    default=current.get(
                        CONF_VERIFICATION_TOLERANCE,
                        DEFAULT_VERIFICATION_TOLERANCE,
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(TOLERANCE_PROFILES),
                        translation_key="verification_tolerance",
                    )
                ),
                vol.Required(
                    CONF_COMMAND_EXPIRY,
                    default=current.get(CONF_COMMAND_EXPIRY, DEFAULT_COMMAND_EXPIRY),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_COMMAND_EXPIRY,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PERSISTENT_RETRY,
                    default=current.get(
                        CONF_PERSISTENT_RETRY, DEFAULT_PERSISTENT_RETRY
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_DIAGNOSTIC_ATTRIBUTES,
                    default=current.get(
                        CONF_DIAGNOSTIC_ATTRIBUTES,
                        DEFAULT_DIAGNOSTIC_ATTRIBUTES,
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EMIT_EVENTS,
                    default=current.get(CONF_EMIT_EVENTS, DEFAULT_EMIT_EVENTS),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
